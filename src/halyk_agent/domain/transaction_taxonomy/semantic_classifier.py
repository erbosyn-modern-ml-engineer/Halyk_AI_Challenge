"""Bounded semantic fallback for ledger descriptions unresolved by deterministic rules."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from halyk_agent.config import Settings
from halyk_agent.domain.covenants.ast import MetricCategory
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
) -> tuple[_CategoryCandidate | None, str]:
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        return None, "MISSING_DEEPSEEK_API_KEY"
    if settings.llm_primary_provider.casefold() != "deepseek":
        return None, "PRIMARY_PROVIDER_NOT_DEEPSEEK"
    try:
        import httpx
    except ImportError:
        return None, "HTTPX_MISSING"

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
    body = {
        "model": settings.llm_primary_model,
        "temperature": 0.0,
        "max_tokens": min(settings.llm_max_tokens, 512),
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
    }
    try:
        response = httpx.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=body,
            timeout=settings.llm_timeout_seconds,
        )
        response.raise_for_status()
        raw = response.json()
        content = raw["choices"][0]["message"]["content"]
        candidate = _CategoryCandidate.model_validate_json(content)
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
        return None, f"PROVIDER_ERROR:{exc.__class__.__name__}"

    if candidate.source_text != row.description:
        return None, "SOURCE_TEXT_MISMATCH"
    if candidate.category == "UNKNOWN":
        return None, "MODEL_UNKNOWN"
    if candidate.confidence != "HIGH":
        return None, "MODEL_NOT_HIGH_CONFIDENCE"
    try:
        category = MetricCategory(candidate.category)
    except ValueError:
        return None, "CATEGORY_OUTSIDE_ENUM"
    return candidate.model_copy(update={"category": category.value}), "OK"


def classify_unresolved_rows(
    rows: tuple[LedgerRow, ...],
    links: tuple[TransactionEntityLink, ...],
    *,
    settings: Settings,
) -> SemanticClassificationBatch:
    """Ask DeepSeek only about scenario-linked rows that deterministic rules left unresolved."""
    if not settings.semantic_fallback_enabled:
        return SemanticClassificationBatch(overrides={}, diagnostics=(), model_calls=0)

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
        if calls >= settings.llm_max_external_attempts:
            diagnostics.append(
                {"transaction_id": row.txn_id, "status": "SKIPPED", "reason": "BUDGET_EXHAUSTED"}
            )
            continue
        calls += 1
        candidate, reason = _request_candidate(row, scenario_id=scenario_id, settings=settings)
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
