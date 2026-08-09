"""Acceptance gates for the bounded DeepSeek covenant planner (Stage 5D)."""

from __future__ import annotations

from typing import Any

from halyk_agent.config import Settings
from halyk_agent.domain.covenants.ast import AccountingScope, MetricCategory
from halyk_agent.domain.covenants.semantic_plan import propose_plan
from halyk_agent.domain.models_gateway.semantic_json import (
    SemanticJsonResult,
    SemanticJsonState,
)

CLAUSE = (
    "The Borrower shall not permit Marketing spend to exceed $300,000.00 in any "
    "financial quarter during the period from 2025-01-01 to 2025-12-31."
)


class _StubGateway:
    """Returns one canned payload; records whether it was consulted."""

    def __init__(self, payload: dict[str, Any] | None, *, state=SemanticJsonState.RESOLVED) -> None:
        self.payload = payload
        self.state = state
        self.calls = 0

    def propose(self, **_kwargs: Any) -> SemanticJsonResult:
        self.calls += 1
        return SemanticJsonResult(
            state=self.state,
            payload=self.payload,
            reason_code="OK" if self.payload is not None else "PROVIDER_ERROR",
            model_called=True,
        )


def _settings() -> Settings:
    return Settings(semantic_fallback_enabled=True)


def _money(value: str) -> dict[str, Any]:
    return {"quantity_type": "MONEY", "value": value, "currency": "USD"}


def _marketing(scope: str = "BORROWER") -> dict[str, Any]:
    return {
        "kind": "sum",
        "of": {"kind": "transaction_set", "selector": {"category": "MARKETING", "scope": scope}},
    }


def _valid_payload(**overrides: Any) -> dict[str, Any]:
    actual = {
        "kind": "period_aggregate",
        "of": _marketing(),
        "grouping": "FINANCIAL_QUARTER",
        "reducer": "MAX",
        "basis": "CASH_DATE",
    }
    payload: dict[str, Any] = {
        "reported_actual": actual,
        "activation_condition": {"kind": "always"},
        "breach_condition": {
            "kind": "compare",
            "left": actual,
            "comparator": "GT",
            "right": {"kind": "constant", "quantity": _money("300000")},
        },
        "period_basis": "CASH_DATE",
        "period_grouping": "FINANCIAL_QUARTER",
        "source_quotes": ["Marketing spend to exceed $300,000.00", "in any financial quarter"],
        "confidence": "HIGH",
        "reason": "quarterly marketing ceiling",
    }
    payload.update(overrides)
    return payload


def _propose(payload: dict[str, Any] | None, **kwargs: Any):
    gateway = _StubGateway(payload, **kwargs)
    return propose_plan(
        CLAUSE, scenario_id="T1", clause_id="6.1", settings=_settings(), gateway=gateway
    )


def test_valid_candidate_accepted() -> None:
    result = _propose(_valid_payload())
    assert result.plan is not None
    assert result.diagnostic["status"] == "ACCEPTED"
    categories = {fact.category for fact in result.plan.required_facts}
    assert MetricCategory.MARKETING in categories


def test_quote_not_in_source_is_rejected() -> None:
    result = _propose(_valid_payload(source_quotes=["a quote that never appears"]))
    assert result.plan is None
    assert result.diagnostic["reason"].startswith("SOURCE_QUOTE_NOT_EXACT")


def test_quote_grounding_tolerates_pdf_whitespace_only() -> None:
    """Extraction inserts runs of spaces mid-sentence; characters must still match."""
    spaced = _propose(_valid_payload(source_quotes=["Marketing  spend   to exceed $300,000.00"]))
    assert spaced.plan is not None
    # Reordered or altered characters remain a rejection.
    altered = _propose(_valid_payload(source_quotes=["Marketing spend to exceed $400,000.00"]))
    assert altered.plan is None
    assert altered.diagnostic["reason"].startswith("SOURCE_QUOTE_NOT_EXACT")


def test_missing_quote_is_rejected() -> None:
    result = _propose(_valid_payload(source_quotes=[]))
    assert result.plan is None
    assert result.diagnostic["reason"] == "SOURCE_QUOTE_MISSING"


def test_low_confidence_is_rejected() -> None:
    result = _propose(_valid_payload(confidence="MEDIUM"))
    assert result.plan is None
    assert result.diagnostic["reason"] == "MODEL_NOT_HIGH_CONFIDENCE"


def test_unknown_node_kind_is_rejected() -> None:
    result = _propose(_valid_payload(reported_actual={"kind": "regression", "of": _marketing()}))
    assert result.plan is None
    assert result.diagnostic["reason"].startswith("AST_INVALID")


def test_unknown_metric_category_is_rejected() -> None:
    bad = {
        "kind": "sum",
        "of": {
            "kind": "transaction_set",
            "selector": {"category": "CRYPTO_MINING", "scope": "BORROWER"},
        },
    }
    result = _propose(_valid_payload(reported_actual=bad))
    assert result.plan is None
    assert result.diagnostic["reason"].startswith("AST_INVALID")


def test_unknown_scope_enum_is_rejected() -> None:
    result = _propose(_valid_payload(reported_actual=_marketing(scope="MARS_BRANCH")))
    assert result.plan is None
    assert result.diagnostic["reason"].startswith("AST_INVALID")


def test_type_incompatible_comparison_is_rejected() -> None:
    payload = _valid_payload(
        breach_condition={
            "kind": "compare",
            "left": _marketing(),
            "comparator": "GT",
            "right": {"kind": "constant", "quantity": {"quantity_type": "RATIO", "value": "3.0"}},
        }
    )
    result = _propose(payload)
    assert result.plan is None
    assert result.diagnostic["reason"].startswith("TYPE_ERROR")


def test_extra_top_level_field_is_rejected() -> None:
    result = _propose(_valid_payload(actual_value="1234.00"))
    assert result.plan is None
    assert result.diagnostic["reason"].startswith("CANDIDATE_SCHEMA_INVALID")


def test_provider_failure_is_unresolved_not_guessed() -> None:
    result = _propose(None, state=SemanticJsonState.PROVIDER_ERROR)
    assert result.plan is None


def test_disabled_fallback_never_calls_the_model() -> None:
    gateway = _StubGateway(_valid_payload())
    result = propose_plan(
        CLAUSE,
        scenario_id="T1",
        clause_id="6.1",
        settings=Settings(semantic_fallback_enabled=False),
        gateway=gateway,
    )
    assert result.plan is None
    assert result.model_called is False
    assert gateway.calls == 0


def test_group_scope_candidate_keeps_scope_identity() -> None:
    payload = _valid_payload(reported_actual=_marketing(scope="GROUP"))
    payload["breach_condition"]["left"] = _marketing(scope="GROUP")
    result = _propose(payload)
    assert result.plan is not None
    assert result.plan.required_facts[0].scope is AccountingScope.GROUP
