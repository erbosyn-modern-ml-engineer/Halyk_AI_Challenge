"""Stage 6 must fail closed — never approximate — on DSL v2 plan semantics."""

from __future__ import annotations

from decimal import Decimal

import pytest

from halyk_agent.domain.covenant_evaluation.planner import (
    EvaluationPlanningError,
    plan_definition,
)
from halyk_agent.domain.covenants.ast import (
    Always,
    Comparator,
    Compare,
    Constant,
    MetricCategory,
    PeriodAggregate,
    PeriodGrouping,
    PeriodReducer,
    Sum,
    TransactionSelector,
    TransactionSet,
)
from halyk_agent.domain.covenants.models import (
    BoundaryInclusivity,
    CovenantDefinition,
    CovenantEvidenceRefs,
    CovenantPlan,
    PeriodDefinition,
    PeriodKind,
    ScopeDefinition,
    ScopeKind,
)
from halyk_agent.domain.covenants.plans import finalize_plan, simple_plan
from halyk_agent.domain.covenants.quantity import QuantityType, TypedQuantity


def _money(value: str) -> TypedQuantity:
    return TypedQuantity(quantity_type=QuantityType.MONEY, value=Decimal(value), currency="USD")


def _capex() -> Sum:
    return Sum(of=TransactionSet(selector=TransactionSelector(category=MetricCategory.CAPEX)))


def _definition(plan: CovenantPlan, *, comparator=None, threshold=None) -> CovenantDefinition:
    return CovenantDefinition(
        definition_id="d1",
        scenario_id="S1",
        clause_id="6.1",
        document_id="doc1",
        document_version_id="v1",
        source_file="a.pdf",
        source_sha256="a" * 64,
        family_id="TEST",
        metric=plan.reported_actual,
        metric_quantity_type=plan.reported_actual_quantity_type,
        comparator=comparator,
        threshold=threshold,
        period=PeriodDefinition(
            period_kind=PeriodKind.CLOSED_INTERVAL,
            start_date=None,
            end_date=None,
            start_inclusive=BoundaryInclusivity.INCLUSIVE,
            end_inclusive=BoundaryInclusivity.INCLUSIVE,
        ),
        scope=ScopeDefinition(scope_kind=ScopeKind.BORROWER),
        plan=plan,
        evidence=CovenantEvidenceRefs(),
        rendered="test",
    )


def test_simple_plan_still_plans_through_the_legacy_path() -> None:
    plan = simple_plan(
        metric=_capex(), compliance_comparator=Comparator.LTE, threshold=_money("2000000")
    )
    primary = plan.breach_condition
    assert isinstance(primary, Compare)
    evaluation = plan_definition(
        _definition(plan, comparator=Comparator.LTE, threshold=_money("2000000"))
    )
    assert evaluation.comparator is Comparator.LTE


def test_compound_breach_is_refused_rather_than_approximated() -> None:
    """A plan with no legacy triple must not be evaluated as if it had one."""
    plan = finalize_plan(
        CovenantPlan(
            reported_actual=_capex(),
            reported_actual_quantity_type=QuantityType.MONEY,
            activation_condition=Always(),
            breach_condition=Compare(
                left=_capex(),
                comparator=Comparator.GT,
                right=Sum(
                    of=TransactionSet(selector=TransactionSelector(category=MetricCategory.REVENUE))
                ),
            ),
        )
    )
    with pytest.raises(EvaluationPlanningError) as excinfo:
        plan_definition(_definition(plan))
    assert excinfo.value.code == "PLAN_REQUIRES_V2_EVALUATOR"


def test_period_aggregation_names_its_downstream_blocker() -> None:
    worst_quarter = PeriodAggregate(
        of=_capex(),
        grouping=PeriodGrouping.FINANCIAL_QUARTER,
        reducer=PeriodReducer.MAX,
    )
    plan = simple_plan(
        metric=worst_quarter,
        compliance_comparator=Comparator.LTE,
        threshold=_money("300000"),
    )
    with pytest.raises(EvaluationPlanningError) as excinfo:
        plan_definition(_definition(plan, comparator=Comparator.LTE, threshold=_money("300000")))
    # The covenant must not silently collapse into an annual total.
    assert excinfo.value.code == "PERIOD_INPUTS_REQUIRED"
    assert isinstance(Constant(quantity=_money("1")), Constant)
