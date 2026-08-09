"""Deterministic clause-structure parsing into typed plans (Stage 5D).

Clause texts here are synthetic paraphrases of the structural classes the parser
must handle. They exercise structure, never a particular scenario or borrower.
"""

# Multilingual covenant vocabulary is intentional.

from __future__ import annotations

from halyk_agent.domain.covenants.ast import (
    AccountingScope,
    Always,
    Comparator,
    Compare,
    Constant,
    MetricCategory,
    PeriodAggregate,
    PeriodReducer,
)
from halyk_agent.domain.covenants.plans import derive_primary_comparison
from halyk_agent.domain.covenants.structure import find_metric_hits, match_structure


def test_springing_conditional_splits_activation_from_breach() -> None:
    text = (
        "Если Коэффициент долговой нагрузки Заёмщика (отношение совокупного долга к "
        "EBITDA) превышает 3.00x, то Заёмщик обязуется не допускать превышения "
        "совокупными Капитальными затратами за период величины $2,500,000.00."
    )
    match = match_structure(text)
    assert match is not None
    assert match.family_id == "SPRINGING_CONDITIONAL"
    plan = match.plan
    # Activation is the trigger; the reported actual stays the restricted metric.
    assert isinstance(plan.activation_condition, Compare)
    assert isinstance(plan.breach_condition, Compare)
    categories = {fact.category for fact in plan.required_facts}
    assert MetricCategory.CAPEX in categories
    # An inactive covenant still has a number to report.
    assert derive_primary_comparison(plan) is not None


def test_springing_else_branch_alone_is_not_an_unconditional_restriction() -> None:
    """ "…ограничение не применяется" must not compile as a covenant by itself."""
    text = (
        "Пока Коэффициент долговой нагрузки не превышает 3.00x, "
        "указанное ограничение не применяется."
    )
    assert match_structure(text) is None


def test_quarterly_ceiling_reports_worst_quarter() -> None:
    text = (
        "Заёмщик не вправе допускать, чтобы Маркетинговые расходы за каждый финансовый "
        "квартал превышали $300,000.00."
    )
    match = match_structure(text)
    assert match is not None
    assert match.family_id == "PERIOD_EXTREMA"
    actual = match.plan.reported_actual
    assert isinstance(actual, PeriodAggregate)
    # A ceiling over every quarter is the worst quarter, not the annual total.
    assert actual.reducer is PeriodReducer.MAX


def test_quarterly_floor_reports_weakest_quarter() -> None:
    text = (
        "Заёмщик обязуется обеспечить, чтобы Выручка за каждый финансовый квартал "
        "составляла не менее $4,000,000.00."
    )
    match = match_structure(text)
    assert match is not None
    actual = match.plan.reported_actual
    assert isinstance(actual, PeriodAggregate)
    assert actual.reducer is PeriodReducer.MIN


def test_dynamic_threshold_produces_expression_valued_right_side() -> None:
    text = (
        "Заёмщик не вправе допускать, чтобы совокупные Арендные платежи за период "
        "превышали 5 процентов Консолидированных капитальных затрат Группы."
    )
    match = match_structure(text)
    assert match is not None
    breach = match.plan.breach_condition
    assert isinstance(breach, Compare)
    # There is no fixed numeric threshold to reduce to.
    assert not isinstance(breach.right, Constant)
    assert derive_primary_comparison(match.plan) is None
    scopes = {fact.scope for fact in match.plan.required_facts}
    assert AccountingScope.GROUP in scopes


def test_group_scope_is_read_from_the_clause() -> None:
    hits = find_metric_hits("Консолидированные капитальные затраты Группы за период")
    assert hits
    assert hits[0].category is MetricCategory.CAPEX
    assert hits[0].scope is AccountingScope.GROUP


def test_borrower_scope_is_the_default() -> None:
    hits = find_metric_hits("Капитальные затраты Заёмщика за период")
    assert hits[0].scope is AccountingScope.BORROWER


def test_unrecognized_wording_defers_rather_than_guessing() -> None:
    """Unknown structure returns None so the bounded planner can take over."""
    assert match_structure("The borrower shall maintain quantum flux below 3 bananas.") is None


def test_structure_plans_are_always_type_checked() -> None:
    text = (
        "Если Коэффициент долговой нагрузки превышает 2.40x, то Заёмщик не вправе "
        "допускать превышения Капитальными затратами величины $5,050,000.00."
    )
    match = match_structure(text)
    assert match is not None
    assert isinstance(match.plan.activation_condition, Compare | Always)
    assert match.plan.required_facts
    assert match.plan.reported_actual_quantity_type.value in {"MONEY", "RATIO"}


def test_activation_comparator_direction_is_preserved() -> None:
    text = (
        "Если Выручка за период составляет менее $4,000,000.00, то Заёмщик обязуется "
        "не допускать превышения Капитальными затратами величины $1,000,000.00."
    )
    match = match_structure(text)
    assert match is not None
    activation = match.plan.activation_condition
    assert isinstance(activation, Compare)
    assert activation.comparator is Comparator.LT
