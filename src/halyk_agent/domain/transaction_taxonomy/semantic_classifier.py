"""Bounded semantic fallback for ledger descriptions unresolved by deterministic rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from halyk_agent.config import Settings
from halyk_agent.domain.covenants.ast import MetricCategory
from halyk_agent.domain.ids import sha256_text
from halyk_agent.domain.models_gateway.semantic_json import SemanticJsonGateway, SemanticJsonState
from halyk_agent.domain.routing.models import LedgerRow, TransactionEntityLink
from halyk_agent.domain.transaction_taxonomy.classify import classify_description


class _CategoryCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str
    confidence: Literal["HIGH", "MEDIUM", "LOW"]
    source_text: str
    reason: str


@dataclass(frozen=True, slots=True)
class SemanticClassificationBatch:
    overrides: dict[str, MetricCategory]
    diagnostics: tuple[dict[str, Any], ...]
    model_calls: int


_SYSTEM = (
    "You are a bounded accounting-description classifier. The deterministic classifier has "
    "already failed. Choose exactly one category from the supplied enum or UNKNOWN. "
    "Do not change transaction ids, amounts, currencies, dates, counterparties, or scenario. "
    "Do not calculate anything. If the description is genuinely ambiguous, choose UNKNOWN. "
    "Return one JSON object only. source_text must exactly equal the supplied description."
)


def _request_candidate(
    row: LedgerRow,
    *,
    scenario_id: str,
    settings: Settings,
    gateway: SemanticJsonGateway,
) -> tuple[_CategoryCandidate | None, str, bool]:
    categories = [item.value for item in MetricCategory]
    payload = {
        "transaction_id": row.txn_id,
        "scenario_id": scenario_id,
        "account_id": row.account_id,
        "counterparty": row.counterparty,
        "description": row.description,
        "currency": row.currency,
        "allowed_categories": [*categories, "UNKNOWN"],
        "output_schema": {
            "category": "one allowed category or UNKNOWN",
            "confidence": "HIGH|MEDIUM|LOW",
            "source_text": "exact description string",
            "reason": "short semantic classification reason",
        },
    }
    response = gateway.propose(
        task_id=f"transaction-category:{scenario_id}:{row.txn_id}",
        prompt_version="transaction-semantic-category-v1",
        schema_version="metric-category-v1",
        source_sha256=sha256_text(row.description),
        system_prompt=_SYSTEM,
        request_payload=payload,
        max_tokens=512,
    )
    if (
        response.state not in {SemanticJsonState.RESOLVED, SemanticJsonState.CACHE_HIT}
        or response.payload is None
    ):
        return None, response.reason_code, response.model_called
    try:
        candidate = _CategoryCandidate.model_validate(response.payload)
    except ValidationError as exc:
        return None, f"CANDIDATE_SCHEMA_INVALID:{exc.__class__.__name__}", response.model_called

    if candidate.source_text != row.description:
        return None, "SOURCE_TEXT_MISMATCH", response.model_called
    if candidate.category == "UNKNOWN":
        return None, "MODEL_UNKNOWN", response.model_called
    if candidate.confidence != "HIGH":
        return None, "MODEL_NOT_HIGH_CONFIDENCE", response.model_called
    try:
        category = MetricCategory(candidate.category)
    except ValueError:
        return None, "CATEGORY_OUTSIDE_ENUM", response.model_called
    return candidate.model_copy(update={"category": category.value}), "OK", response.model_called


def classify_unresolved_rows(
    rows: tuple[LedgerRow, ...],
    links: tuple[TransactionEntityLink, ...],
    *,
    settings: Settings,
    gateway: SemanticJsonGateway | None = None,
) -> SemanticClassificationBatch:
    """Ask DeepSeek only about scenario-linked rows that deterministic rules left unresolved."""
    if not settings.semantic_fallback_enabled:
        return SemanticClassificationBatch(overrides={}, diagnostics=(), model_calls=0)

    semantic_gateway = gateway or SemanticJsonGateway(settings=settings)

    scenario_by_txn = {link.txn_id: link.scenario_id for link in links}
    overrides: dict[str, MetricCategory] = {}
    diagnostics: list[dict[str, Any]] = []
    calls = 0
    for row in sorted(rows, key=lambda item: (item.row_index, item.txn_id)):
        scenario_id = scenario_by_txn.get(row.txn_id)
        if scenario_id is None:
            continue
        deterministic = classify_description(row.description)
        if deterministic.status != "UNRESOLVED":
            continue
        candidate, reason, model_called = _request_candidate(
            row,
            scenario_id=scenario_id,
            settings=settings,
            gateway=semantic_gateway,
        )
        calls += int(model_called)
        if candidate is None:
            diagnostics.append(
                {"transaction_id": row.txn_id, "status": "UNRESOLVED", "reason": reason}
            )
            continue
        category = MetricCategory(candidate.category)
        overrides[row.txn_id] = category
        diagnostics.append(
            {
                "transaction_id": row.txn_id,
                "scenario_id": scenario_id,
                "status": "ACCEPTED",
                "category": category.value,
                "source_text": candidate.source_text,
                "reason": candidate.reason,
            }
        )
    return SemanticClassificationBatch(
        overrides=overrides,
        diagnostics=tuple(diagnostics),
        model_calls=calls,
    )
