"""Bounded semantic recovery for covenant formulas that deterministic rules cannot parse."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from halyk_agent.config import Settings
from halyk_agent.domain.covenants.ast import EXPR_ADAPTER, MetricCategory, infer_quantity_type
from halyk_agent.domain.covenants.formulas import FormulaMatch
from halyk_agent.domain.covenants.quantity import CovenantTypeError


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
) -> SemanticFormulaResult:
    """Return a typed formula only when a high-confidence exact-quote proposal validates locally."""
    if not settings.semantic_fallback_enabled:
        return SemanticFormulaResult(
            formula=None,
            diagnostic={"scenario_id": scenario_id, "clause_id": clause_id, "reason": "DISABLED"},
            model_called=False,
        )
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        return SemanticFormulaResult(
            formula=None,
            diagnostic={
                "scenario_id": scenario_id,
                "clause_id": clause_id,
                "reason": "MISSING_DEEPSEEK_API_KEY",
            },
            model_called=False,
        )
    if settings.llm_primary_provider.casefold() != "deepseek":
        return SemanticFormulaResult(
            formula=None,
            diagnostic={
                "scenario_id": scenario_id,
                "clause_id": clause_id,
                "reason": "PRIMARY_PROVIDER_NOT_DEEPSEEK",
            },
            model_called=False,
        )
    try:
        import httpx
    except ImportError:
        return SemanticFormulaResult(
            formula=None,
            diagnostic={"scenario_id": scenario_id, "clause_id": clause_id, "reason": "HTTPX_MISSING"},
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
    body = {
        "model": settings.llm_primary_model,
        "temperature": 0.0,
        "max_tokens": min(settings.llm_max_tokens, 1400),
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
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
        content = response.json()["choices"][0]["message"]["content"]
        candidate = _FormulaCandidate.model_validate_json(content)
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
        return SemanticFormulaResult(
            formula=None,
            diagnostic={
                "scenario_id": scenario_id,
                "clause_id": clause_id,
                "reason": f"PROVIDER_OR_SCHEMA_ERROR:{exc.__class__.__name__}",
            },
            model_called=True,
        )

    if candidate.confidence != "HIGH":
        return SemanticFormulaResult(
            formula=None,
            diagnostic={
                "scenario_id": scenario_id,
                "clause_id": clause_id,
                "reason": "MODEL_NOT_HIGH_CONFIDENCE",
            },
            model_called=True,
        )
    if not candidate.source_quote or candidate.source_quote not in clause_text:
        return SemanticFormulaResult(
            formula=None,
            diagnostic={
                "scenario_id": scenario_id,
                "clause_id": clause_id,
                "reason": "SOURCE_QUOTE_NOT_EXACT",
            },
            model_called=True,
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
            model_called=True,
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
