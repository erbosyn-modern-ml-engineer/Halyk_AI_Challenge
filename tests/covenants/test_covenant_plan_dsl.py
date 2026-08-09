"""Covenant DSL v2 semantics: plans, boolean logic, scope, periods (Stage 5D)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from halyk_agent.domain.covenants.ast import (
    AccountingScope,
    Add,
    Always,
    And,
    Comparator,
    Compare,
    Constant,
    Divide,
    MetricCategory,
    Min,
    Multiply,
    Not,
    Or,
    PeriodAggregate,
    PeriodBasis,
    PeriodGrouping,
    PeriodReducer,
    Subtract,
    Sum,
    TransactionSelector,
    TransactionSet,
    infer_quantity_type,
    validate_bool_expr,
)
from halyk_agent.domain.covenants.models import CovenantPlan, RequiredFactSource
from halyk_agent.domain.covenants.plans import (
    derive_primary_comparison,
    derive_required_facts,
    finalize_plan,
    simple_plan,
)
from halyk_agent.domain.covenants.quantity import CovenantTypeError, QuantityType, TypedQuantity


def money(value: str) -> TypedQuantity:
    return TypedQuantity(quantity_type=QuantityType.MONEY, value=Decimal(value), currency="USD")


def ratio(value: str) -> TypedQuantity:
    return TypedQuantity(quantity_type=QuantityType.RATIO, value=Decimal(value))


def metric(category: MetricCategory, scope: AccountingScope = AccountingScope.BORROWER) -> Sum:
    return Sum(
        of=TransactionSet(
            selector=TransactionSelector(
                category=category,
                scope=scope,
                group_level=scope is not AccountingScope.BORROWER,
            )
        )
    )


def ebitda(scope: AccountingScope = AccountingScope.BORROWER) -> Subtract:
    return Subtract(
        left=metric(MetricCategory.REVENUE, scope), right=metric(MetricCategory.OPEX, scope)
    )


def test_simple_covenant_reduces_to_legacy_triple() -> None:
    plan = simple_plan(
        metric=metric(MetricCategory.CAPEX),
        compliance_comparator=Comparator.LTE,
        threshold=money("2000000"),
    )
    assert isinstance(plan.activation_condition, Always)
    assert isinstance(plan.breach_condition, Compare)
    # Breach is stated in the breach direction, not the compliance direction.
    assert plan.breach_condition.comparator is Comparator.GT
    primary = derive_primary_comparison(plan)
    assert primary is not None
    assert primary[0] is Comparator.LTE
    assert primary[1].value == Decimal("2000000")


def test_compound_and_default_does_not_reduce_and_keeps_reported_actual() -> None:
    """Neither leg alone is a breach; the reported actual is not the boolean."""
    leverage = Divide(numerator=metric(MetricCategory.FINANCIAL_DEBT), denominator=ebitda())
    capex = metric(MetricCategory.CAPEX)
    plan = finalize_plan(
        CovenantPlan(
            reported_actual=capex,
            reported_actual_quantity_type=QuantityType.MONEY,
            activation_condition=Always(),
            breach_condition=And(
                args=(
                    Compare(
                        left=leverage,
                        comparator=Comparator.GT,
                        right=Constant(quantity=ratio("3.50")),
                    ),
                    Compare(
                        left=capex,
                        comparator=Comparator.GT,
                        right=Constant(quantity=money("2000000")),
                    ),
                )
            ),
        )
    )
    assert plan.reported_actual == capex
    # A compound breach must not be flattened into a single comparator/threshold.
    assert derive_primary_comparison(plan) is None


def test_compound_or_default() -> None:
    leverage = Divide(numerator=metric(MetricCategory.FINANCIAL_DEBT), denominator=ebitda())
    dscr = Divide(numerator=ebitda(), denominator=metric(MetricCategory.INTEREST_EXPENSE))
    plan = finalize_plan(
        CovenantPlan(
            reported_actual=dscr,
            reported_actual_quantity_type=QuantityType.RATIO,
            activation_condition=Always(),
            breach_condition=Or(
                args=(
                    Compare(
                        left=leverage,
                        comparator=Comparator.GT,
                        right=Constant(quantity=ratio("3.00")),
                    ),
                    Compare(
                        left=dscr, comparator=Comparator.LT, right=Constant(quantity=ratio("1.30"))
                    ),
                )
            ),
        )
    )
    assert isinstance(plan.breach_condition, Or)
    assert plan.reported_actual == dscr
    assert derive_primary_comparison(plan) is None


def test_springing_activation_is_separate_from_breach() -> None:
    """Inactive, active-compliant and active-breach are three distinct states."""
    leverage = Divide(numerator=metric(MetricCategory.FINANCIAL_DEBT), denominator=ebitda())
    capex = metric(MetricCategory.CAPEX)
    plan = finalize_plan(
        CovenantPlan(
            reported_actual=capex,
            reported_actual_quantity_type=QuantityType.MONEY,
            activation_condition=Compare(
                left=leverage, comparator=Comparator.GT, right=Constant(quantity=ratio("3.00"))
            ),
            breach_condition=Compare(
                left=capex, comparator=Comparator.GT, right=Constant(quantity=money("2500000"))
            ),
        )
    )
    assert isinstance(plan.activation_condition, Compare)
    # The reported actual stays CAPEX whether or not the covenant is active, so
    # an inactive covenant still has a number to report.
    assert plan.reported_actual == capex
    assert derive_primary_comparison(plan) is not None


def test_dynamic_threshold_is_an_expression() -> None:
    """Interest + Rent <= 5% of Group CAPEX — no fixed numeric threshold exists."""
    left = Add(
        left=metric(MetricCategory.INTEREST_EXPENSE),
        right=metric(MetricCategory.RENT),
    )
    right = Multiply(
        left=Constant(quantity=ratio("0.05")),
        right=metric(MetricCategory.CAPEX, AccountingScope.GROUP),
    )
    plan = finalize_plan(
        CovenantPlan(
            reported_actual=left,
            reported_actual_quantity_type=QuantityType.MONEY,
            activation_condition=Always(),
            breach_condition=Compare(left=left, comparator=Comparator.GT, right=right),
        )
    )
    assert derive_primary_comparison(plan) is None
    scopes = {fact.scope for fact in plan.required_facts}
    assert AccountingScope.GROUP in scopes


def test_capped_addback_basket() -> None:
    """Add-backs count only up to 5% of revenue."""
    revenue = metric(MetricCategory.REVENUE)
    cap = Multiply(left=Constant(quantity=ratio("0.05")), right=revenue)
    allowed = Min(args=(metric(MetricCategory.ONE_TIME_ADD_BACKS), cap))
    adjusted = Add(left=ebitda(), right=allowed)
    assert infer_quantity_type(adjusted) is QuantityType.MONEY


def test_permitted_basket_net_of_cap() -> None:
    """RP total minus eligible consulting capped at the basket size."""
    rp = metric(MetricCategory.RELATED_PARTY_PAYMENTS)
    eligible = metric(MetricCategory.CONSULTING_SERVICES)
    basket = Min(args=(eligible, Constant(quantity=money("300000"))))
    net = Subtract(left=rp, right=basket)
    plan = simple_plan(metric=net, compliance_comparator=Comparator.LTE, threshold=money("250000"))
    categories = {fact.category for fact in plan.required_facts}
    assert MetricCategory.CONSULTING_SERVICES in categories
    assert MetricCategory.RELATED_PARTY_PAYMENTS in categories


def test_quarterly_maximum_is_not_the_annual_sum() -> None:
    worst_quarter = PeriodAggregate(
        of=metric(MetricCategory.MARKETING),
        grouping=PeriodGrouping.FINANCIAL_QUARTER,
        reducer=PeriodReducer.MAX,
    )
    plan = simple_plan(
        metric=worst_quarter, compliance_comparator=Comparator.LTE, threshold=money("300000")
    )
    assert isinstance(plan.reported_actual, PeriodAggregate)
    assert plan.reported_actual.reducer is PeriodReducer.MAX
    assert infer_quantity_type(worst_quarter) is QuantityType.MONEY
    fact = next(f for f in plan.required_facts if f.category is MetricCategory.MARKETING)
    assert fact.grouping is PeriodGrouping.FINANCIAL_QUARTER


def test_quarterly_minimum_with_accounting_recognition_basis() -> None:
    weakest_quarter = PeriodAggregate(
        of=ebitda(),
        grouping=PeriodGrouping.FINANCIAL_QUARTER,
        reducer=PeriodReducer.MIN,
        basis=PeriodBasis.ACCOUNTING_RECOGNITION,
    )
    plan = simple_plan(
        metric=weakest_quarter, compliance_comparator=Comparator.GTE, threshold=money("600000")
    )
    assert plan.reported_actual.reducer is PeriodReducer.MIN
    bases = {fact.basis for fact in plan.required_facts}
    assert PeriodBasis.ACCOUNTING_RECOGNITION in bases


def test_scope_is_part_of_metric_identity() -> None:
    borrower = metric(MetricCategory.CAPEX)
    group = metric(MetricCategory.CAPEX, AccountingScope.GROUP)
    assert borrower != group
    assert (
        derive_required_facts(
            simple_plan(
                metric=group, compliance_comparator=Comparator.LTE, threshold=money("20000000")
            )
        )[0].scope
        is AccountingScope.GROUP
    )


def test_mixed_scope_arithmetic_keeps_both_scopes() -> None:
    """Group CAPEX outside borrower = Group CAPEX - Borrower CAPEX."""
    outside = Subtract(
        left=metric(MetricCategory.CAPEX, AccountingScope.GROUP),
        right=metric(MetricCategory.CAPEX),
    )
    plan = simple_plan(
        metric=outside, compliance_comparator=Comparator.LTE, threshold=money("1000000")
    )
    scopes = {fact.scope for fact in plan.required_facts}
    assert scopes == {AccountingScope.GROUP, AccountingScope.BORROWER}


def test_mixed_scope_ratio_is_not_single_scope() -> None:
    """Group CAPEX / Borrower EBITDA must not collapse to one scope."""
    mixed = Divide(
        numerator=metric(MetricCategory.CAPEX, AccountingScope.GROUP),
        denominator=ebitda(AccountingScope.BORROWER),
    )
    facts = derive_required_facts(
        simple_plan(metric=mixed, compliance_comparator=Comparator.LTE, threshold=ratio("2.75"))
    )
    capex = next(f for f in facts if f.category is MetricCategory.CAPEX)
    revenue = next(f for f in facts if f.category is MetricCategory.REVENUE)
    assert capex.scope is AccountingScope.GROUP
    assert revenue.scope is AccountingScope.BORROWER


def test_group_scope_facts_are_document_sourced() -> None:
    plan = simple_plan(
        metric=metric(MetricCategory.CAPEX, AccountingScope.GROUP),
        compliance_comparator=Comparator.LTE,
        threshold=money("20000000"),
    )
    assert plan.required_facts[0].source is RequiredFactSource.DOCUMENT_DISCLOSURE


def test_unavailable_fact_is_requested_not_substituted() -> None:
    """DSCR needs scheduled principal, which is not financing inflows."""
    dscr = Divide(
        numerator=ebitda(),
        denominator=Add(
            left=metric(MetricCategory.INTEREST_EXPENSE),
            right=metric(MetricCategory.SCHEDULED_PRINCIPAL_REPAYMENT),
        ),
    )
    plan = simple_plan(metric=dscr, compliance_comparator=Comparator.GTE, threshold=ratio("1.25"))
    categories = {fact.category for fact in plan.required_facts}
    assert MetricCategory.SCHEDULED_PRINCIPAL_REPAYMENT in categories
    assert MetricCategory.FINANCING_INFLOWS not in categories
    principal = next(
        f for f in plan.required_facts if f.category is MetricCategory.SCHEDULED_PRINCIPAL_REPAYMENT
    )
    assert principal.source is RequiredFactSource.DOCUMENT_DISCLOSURE


def test_nested_derived_metric_fixed_charge_cover() -> None:
    """EBITDAR / Fixed Charges, both derived."""
    ebitdar = Add(left=ebitda(), right=metric(MetricCategory.RENT))
    fixed_charges = Add(
        left=metric(MetricCategory.INTEREST_EXPENSE), right=metric(MetricCategory.RENT)
    )
    cover = Divide(numerator=ebitdar, denominator=fixed_charges)
    assert infer_quantity_type(cover) is QuantityType.RATIO


def test_conditional_proviso_expressed_as_boolean() -> None:
    """Rent over the cap is not a default if insurance is at least the floor."""
    breach = And(
        args=(
            Compare(
                left=metric(MetricCategory.RENT),
                comparator=Comparator.GT,
                right=Constant(quantity=money("1000000")),
            ),
            Not(
                of=Compare(
                    left=metric(MetricCategory.INSURANCE_PREMIUMS),
                    comparator=Comparator.GTE,
                    right=Constant(quantity=money("200000")),
                )
            ),
        )
    )
    validate_bool_expr(breach)


def test_type_error_on_incomparable_sides() -> None:
    bad = Compare(
        left=metric(MetricCategory.CAPEX),
        comparator=Comparator.GT,
        right=Constant(quantity=ratio("3.00")),
    )
    with pytest.raises(CovenantTypeError):
        validate_bool_expr(bad)


def test_percent_threshold_normalizes_to_ratio() -> None:
    percent = TypedQuantity(quantity_type=QuantityType.PERCENT, value=Decimal("80"))
    plan = simple_plan(
        metric=Divide(
            numerator=metric(MetricCategory.CAPEX), denominator=metric(MetricCategory.REVENUE)
        ),
        compliance_comparator=Comparator.LTE,
        threshold=percent,
    )
    primary = derive_primary_comparison(plan)
    assert primary is not None
    assert primary[1].quantity_type is QuantityType.RATIO


def test_legacy_group_level_flag_implies_group_scope() -> None:
    selector = TransactionSelector(category=MetricCategory.GROUP_CAPEX, group_level=True)
    assert selector.scope is AccountingScope.GROUP
