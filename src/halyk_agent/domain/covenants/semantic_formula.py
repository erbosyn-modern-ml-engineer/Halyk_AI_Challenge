"""Bounded semantic recovery for covenant formulas that deterministic rules cannot parse."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from halyk_agent.config import Settings
from halyk_agent.domain.covenants.ast import EXPR_ADAPTER, MetricCategory, infer_quantity_type
from halyk_agent.domain.covenants.formulas import FormulaMatch
from halyk_agent.domain.covenants.quantity import CovenantTypeError
from halyk_agent.domain.ids import sha256_text
from halyk_agent.domain.models_gateway.semantic_json import SemanticJsonGateway, SemanticJsonState


class _FormulaCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: dict[str, Any]
    source_quote: str
    confidence: Literal["HIGH", "MEDIUM", "LOW"]
    reason: str


@dataclass(frozen=True, slots=True)
class SemanticFormulaResult:
    formula: FormulaMatch | None
    diagnostic: dict[str, Any]
    model_called: bool


_SYSTEM = (
    "You are a bounded legal-financial semantic parser. The deterministic covenant parser "
    "could not represent the formula. Return only a candidate expression AST using the supplied "
    "node kinds and metric categories. Do not decide compliance, do not calculate actual values, "
    "do not invent transaction data, FX, dates, thresholds, comparator semantics, or missing facts. "
    "source_quote must be an exact contiguous quote copied from the clause that supports the metric. "
    "If the metric cannot be represented exactly with the supplied DSL, return LOW confidence."
)


def propose_formula(
    clause_text: str,
    *,
    scenario_id: str,
    clause_id: str,
    settings: Settings,
    gateway: SemanticJsonGateway | None = None,
) -> SemanticFormulaResult:
    """Return a typed formula only when a high-confidence exact-quote proposal validates locally."""
    if not settings.semantic_fallback_enabled:
        return SemanticFormulaResult(
            formula=None,
            diagnostic={"scenario_id": scenario_id, "clause_id": clause_id, "reason": "DISABLED"},
            model_called=False,
        )

    schema_hint = {
        "allowed_node_kinds": [
            "constant",
            "transaction_set",
            "sum",
            "count",
            "min",
            "max",
            "add",
            "subtract",
            "multiply",
            "divide",
        ],
        "allowed_metric_categories": [item.value for item in MetricCategory],
        "important": (
            "Use transaction_set with a selector containing category for ledger-backed operands. "
            "Do not create a category or node kind outside these lists."
        ),
        "output": {
            "metric": "typed AST object",
            "source_quote": "exact contiguous source quote",
            "confidence": "HIGH|MEDIUM|LOW",
            "reason": "short explanation",
        },
    }
    request = {
        "scenario_id": scenario_id,
        "clause_id": clause_id,
        "clause_text": clause_text,
        "dsl": schema_hint,
    }
    semantic_gateway = gateway or SemanticJsonGateway(settings=settings)
    response = semantic_gateway.propose(
        task_id=f"covenant-formula:{scenario_id}:{clause_id}",
        prompt_version="covenant-semantic-formula-v1",
        schema_version="covenant-ast-v1",
        source_sha256=sha256_text(clause_text),
        system_prompt=_SYSTEM,
        request_payload=request,
        max_tokens=1400,
    )
    if response.state not in {SemanticJsonState.RESOLVED, SemanticJsonState.CACHE_HIT} or response.payload is None:
        return SemanticFormulaResult(
            formula=None,
            diagnostic={
                "scenario_id": scenario_id,
                "clause_id": clause_id,
                "reason": response.reason_code,
                "gateway_state": response.state.value,
            },
            model_called=response.model_called,
        )
    try:
        candidate = _FormulaCandidate.model_validate(response.payload)
    except ValidationError as exc:
        return SemanticFormulaResult(
            formula=None,
            diagnostic={
                "scenario_id": scenario_id,
                "clause_id": clause_id,
                "reason": f"CANDIDATE_SCHEMA_INVALID:{exc.__class__.__name__}",
            },
            model_called=response.model_called,
        )

    if candidate.confidence != "HIGH":
        return SemanticFormulaResult(
            formula=None,
            diagnostic={
                "scenario_id": scenario_id,
                "clause_id": clause_id,
                "reason": "MODEL_NOT_HIGH_CONFIDENCE",
            },
            model_called=response.model_called,
        )
    if not candidate.source_quote or candidate.source_quote not in clause_text:
        return SemanticFormulaResult(
            formula=None,
            diagnostic={
                "scenario_id": scenario_id,
                "clause_id": clause_id,
                "reason": "SOURCE_QUOTE_NOT_EXACT",
            },
            model_called=response.model_called,
        )
    try:
        metric = EXPR_ADAPTER.validate_python(candidate.metric)
        quantity_type = infer_quantity_type(metric)
    except (ValidationError, CovenantTypeError) as exc:
        return SemanticFormulaResult(
            formula=None,
            diagnostic={
                "scenario_id": scenario_id,
                "clause_id": clause_id,
                "reason": f"AST_VALIDATION_FAILED:{exc.__class__.__name__}",
            },
            model_called=response.model_called,
        )

    formula = FormulaMatch(family_id="DEEPSEEK_TYPED_AST_V1", metric=metric)
    return SemanticFormulaResult(
        formula=formula,
        diagnostic={
            "scenario_id": scenario_id,
            "clause_id": clause_id,
            "status": "ACCEPTED",
            "family_id": formula.family_id,
            "quantity_type": quantity_type.value,
            "source_quote": candidate.source_quote,
            "reason": candidate.reason,
        },
        model_called=True,
    )
