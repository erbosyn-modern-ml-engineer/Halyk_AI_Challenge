"""Execution, fail-closed and Decimal semantics tests for Stage 6."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, localcontext

import pytest

from halyk_agent.domain.covenant_evaluation import (
    ActivationState,
    ComplianceStatus,
    EvaluationExecutor,
    EvaluationStatus,
    EvaluationValidationError,
    plan_definition,
)
from halyk_agent.domain.covenants.ast import Constant, Divide, MetricCategory, Sum, TransactionSet
from halyk_agent.domain.covenants.models import (
    ActivationCondition,
    Comparator,
    CovenantModifier,
    CovenantModifierKind,
    PeriodDefinition,
    PeriodKind,
)
from halyk_agent.domain.covenants.quantity import QuantityType, TypedQuantity
from halyk_agent.domain.transaction_taxonomy.models import SelectorReadinessStatus

from ._helpers import _context, _definition, _input, _ratio_constant, _ratio_definition, _selector


def test_sum_and_compliance_are_decimal_deterministic() -> None:
    selector = _selector()
    definition = _definition(
        Sum(of=TransactionSet(selector=selector)),
        selectors=(selector,),
        threshold=TypedQuantity(
            quantity_type=QuantityType.MONEY,
            value=Decimal("125.50"),
            currency="USD",
        ),
    )
    context = _context(
        definition,
        (
            _input("i1", "100.25"),
            _input("i2", "25.25"),
        ),
    )
    result = EvaluationExecutor().execute(plan_definition(definition), context)
    assert result.status is EvaluationStatus.RESOLVED
    assert result.compliance_status is ComplianceStatus.COMPLIANT
    assert result.actual is not None
    assert result.actual.value == Decimal("125.50")
    assert result.actual.currency == "USD"
    assert result.contributing_transaction_ids == ("TX-i1", "TX-i2")


def test_selector_flags_are_applied_by_stage6_not_silently_ignored() -> None:
    selector = _selector(include_flags=("ELIGIBLE",), exclude_flags=("REVERSED",))
    definition = _definition(
        Sum(of=TransactionSet(selector=selector)),
        selectors=(selector,),
        threshold=TypedQuantity(
            quantity_type=QuantityType.MONEY,
            value=Decimal("10"),
            currency="USD",
        ),
    )
    context = _context(
        definition,
        (
            _input("keep", "20", flags=("ELIGIBLE",)),
            _input("no-flag", "999"),
            _input("excluded", "999", flags=("ELIGIBLE", "REVERSED")),
        ),
    )
    result = EvaluationExecutor().execute(plan_definition(definition), context)
    assert result.status is EvaluationStatus.RESOLVED
    assert result.actual is not None
    assert result.actual.value == Decimal("20")
    assert result.contributing_transaction_ids == ("TX-keep",)


def test_materiality_post_filter_empty_is_true_zero() -> None:
    selector = _selector(MetricCategory.ONE_TIME_ADD_BACKS)
    definition = _definition(
        Sum(of=TransactionSet(selector=selector)),
        selectors=(selector,),
        threshold=TypedQuantity(
            quantity_type=QuantityType.MONEY,
            value=Decimal("1"),
            currency="USD",
        ),
        modifiers=(
            CovenantModifier(
                kind=CovenantModifierKind.MATERIALITY_FLOOR,
                detail="floor",
                threshold=TypedQuantity(
                    quantity_type=QuantityType.MONEY,
                    value=Decimal("300"),
                    currency="USD",
                ),
                applies_to_category=MetricCategory.ONE_TIME_ADD_BACKS,
            ),
        ),
    )
    context = _context(
        definition,
        (
            _input(
                "small",
                "299.99",
                category=MetricCategory.ONE_TIME_ADD_BACKS,
            ),
        ),
    )
    result = EvaluationExecutor().execute(plan_definition(definition), context)
    assert result.status is EvaluationStatus.RESOLVED
    assert result.actual is not None
    assert result.actual.value == Decimal("0")
    assert result.actual.currency == "USD"
    assert result.compliance_status is ComplianceStatus.BREACH
    assert result.contributing_transaction_ids == ()


def test_materiality_trace_excludes_filtered_out_transactions() -> None:
    selector = _selector(MetricCategory.ONE_TIME_ADD_BACKS)
    definition = _definition(
        Sum(of=TransactionSet(selector=selector)),
        selectors=(selector,),
        modifiers=(
            CovenantModifier(
                kind=CovenantModifierKind.MATERIALITY_FLOOR,
                detail="floor",
                threshold=TypedQuantity(
                    quantity_type=QuantityType.MONEY,
                    value=Decimal("300"),
                    currency="USD",
                ),
                applies_to_category=MetricCategory.ONE_TIME_ADD_BACKS,
            ),
        ),
    )
    context = _context(
        definition,
        (
            _input("small", "299", category=MetricCategory.ONE_TIME_ADD_BACKS),
            _input("large", "301", category=MetricCategory.ONE_TIME_ADD_BACKS),
        ),
    )
    result = EvaluationExecutor().execute(plan_definition(definition), context)
    assert result.status is EvaluationStatus.RESOLVED
    assert result.actual is not None
    assert result.actual.value == Decimal("301")
    assert result.contributing_transaction_ids == ("TX-large",)


def test_mixed_currency_fails_closed() -> None:
    selector = _selector()
    definition = _definition(
        Sum(of=TransactionSet(selector=selector)),
        selectors=(selector,),
    )
    context = _context(
        definition,
        (
            _input("usd", "100", currency="USD"),
            _input("eur", "100", currency="EUR"),
        ),
    )
    result = EvaluationExecutor().execute(plan_definition(definition), context)
    assert result.status is EvaluationStatus.UNRESOLVED
    assert "MIXED_CURRENCY_NO_TRUSTED_CONVERSION" in {
        issue.code for issue in result.issues
    }


def test_undecidable_period_is_unresolved_not_silently_dropped() -> None:
    selector = _selector()
    definition = _definition(
        Sum(of=TransactionSet(selector=selector)),
        selectors=(selector,),
    )
    context = _context(
        definition,
        (_input("unknown-date", "100", transaction_date=None),),
    )
    result = EvaluationExecutor().execute(plan_definition(definition), context)
    assert result.status is EvaluationStatus.UNRESOLVED
    assert "PERIOD_MEMBERSHIP_UNDECIDABLE" in {issue.code for issue in result.issues}


def test_zero_denominator_is_error() -> None:
    definition = _ratio_definition("0")
    result = EvaluationExecutor().execute(
        plan_definition(definition),
        _context(definition, ()),
    )
    assert result.status is EvaluationStatus.ERROR
    assert "ZERO_DENOMINATOR" in {issue.code for issue in result.issues}


def test_negative_denominator_is_evaluated_with_diagnostic() -> None:
    definition = _ratio_definition("-2")
    result = EvaluationExecutor().execute(
        plan_definition(definition),
        _context(definition, ()),
    )
    assert result.status is EvaluationStatus.RESOLVED
    assert result.actual is not None
    assert result.actual.value == Decimal("-0.5")
    assert "NEGATIVE_DENOMINATOR" in {issue.code for issue in result.issues}


def test_local_decimal_context_ignores_ambient_process_precision() -> None:
    definition = _definition(
        Divide(
            numerator=_ratio_constant("1"),
            denominator=_ratio_constant("3"),
        ),
        threshold=TypedQuantity(
            quantity_type=QuantityType.RATIO,
            value=Decimal("0"),
        ),
        selectors=(),
    )
    plan = plan_definition(definition)
    context = _context(definition, ())
    with localcontext() as ambient:
        ambient.prec = 4
        low_ambient = EvaluationExecutor().execute(plan, context)
    with localcontext() as ambient:
        ambient.prec = 28
        normal_ambient = EvaluationExecutor().execute(plan, context)
    assert low_ambient.actual is not None
    assert normal_ambient.actual is not None
    assert low_ambient.actual.value == normal_ambient.actual.value
    assert len(str(low_ambient.actual.value).replace(".", "")) >= 50


def test_unresolved_stage5f_readiness_stops_evaluation() -> None:
    selector = _selector()
    definition = _definition(
        Sum(of=TransactionSet(selector=selector)),
        selectors=(selector,),
    )
    context = _context(
        definition,
        (_input("i1", "100"),),
        readiness=SelectorReadinessStatus.UNRESOLVED,
    )
    result = EvaluationExecutor().execute(plan_definition(definition), context)
    assert result.status is EvaluationStatus.UNRESOLVED
    assert "DEFINITION_INPUT_UNRESOLVED" in {issue.code for issue in result.issues}


def test_amount_contract_version_mismatch_is_global_failure() -> None:
    selector = _selector()
    definition = _definition(
        Sum(of=TransactionSet(selector=selector)),
        selectors=(selector,),
    )
    context = _context(definition, (_input("i1", "100"),)).model_copy(
        update={"amount_contract_version": "tampered.contract.v999"}
    )
    with pytest.raises(EvaluationValidationError) as exc:
        EvaluationExecutor().execute(plan_definition(definition), context)
    assert exc.value.code == "AMOUNT_CONTRACT_VERSION_MISMATCH"


def test_unknown_input_scenario_is_global_failure() -> None:
    selector = _selector()
    definition = _definition(
        Sum(of=TransactionSet(selector=selector)),
        selectors=(selector,),
    )
    context = _context(
        definition,
        (_input("foreign", "100", scenario_id="OTHER"),),
    )
    with pytest.raises(EvaluationValidationError) as exc:
        EvaluationExecutor().execute(plan_definition(definition), context)
    assert exc.value.code == "SCENARIO_UNIVERSE_MISMATCH"


def test_inactive_springing_condition_does_not_evaluate_main_metric() -> None:
    financing = _selector(MetricCategory.FINANCING_INFLOWS)
    revenue = _selector(MetricCategory.REVENUE)
    definition = _definition(
        Divide(
            numerator=Sum(of=TransactionSet(selector=revenue)),
            denominator=Constant(
                quantity=TypedQuantity(
                    quantity_type=QuantityType.MONEY,
                    value=Decimal("0"),
                    currency="USD",
                )
            ),
        ),
        threshold=TypedQuantity(
            quantity_type=QuantityType.RATIO,
            value=Decimal("1"),
        ),
        selectors=(revenue, financing),
    )
    definition = definition.model_copy(
        update={
            "activation_condition": ActivationCondition(
                metric=Sum(of=TransactionSet(selector=financing)),
                comparator=Comparator.GT,
                threshold=TypedQuantity(
                    quantity_type=QuantityType.MONEY,
                    value=Decimal("100"),
                    currency="USD",
                ),
            )
        }
    )
    context = _context(
        definition,
        (
            _input("financing", "50", category=MetricCategory.FINANCING_INFLOWS),
            _input("revenue", "100", category=MetricCategory.REVENUE),
        ),
    )
    result = EvaluationExecutor().execute(plan_definition(definition), context)
    assert result.status is EvaluationStatus.NOT_ACTIVATED
    assert result.activation_state is ActivationState.INACTIVE
    assert result.compliance_status is None
    assert "ZERO_DENOMINATOR" not in {issue.code for issue in result.issues}


def test_financial_quarter_uses_quarter_period_helper() -> None:
    selector = _selector()
    definition = _definition(
        Sum(of=TransactionSet(selector=selector)),
        selectors=(selector,),
    ).model_copy(
        update={
            "period": PeriodDefinition(
                period_kind=PeriodKind.FINANCIAL_QUARTER,
                quarter=2,
            )
        }
    )
    context = _context(
        definition,
        (
            _input("q2", "100", transaction_date=date(2025, 5, 1)),
            _input("q3", "999", transaction_date=date(2025, 8, 1)),
        ),
    )
    result = EvaluationExecutor().execute(plan_definition(definition), context)
    assert result.status is EvaluationStatus.RESOLVED
    assert result.actual is not None
    assert result.actual.value == Decimal("100")
    assert result.contributing_transaction_ids == ("TX-q2",)
