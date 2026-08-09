"""CovenantPlan construction, normalization and required-fact derivation (Stage 5D).

The plan is the authoritative covenant semantics. This module keeps the legacy
``metric / comparator / threshold`` triple in sync so existing consumers keep
working, and derives the typed fact universe a plan needs from downstream stages.
"""

from __future__ import annotations

from halyk_agent.domain.covenants.ast import (
    AccountingScope,
    Add,
    Always,
    And,
    BoolExpr,
    Comparator,
    Compare,
    Constant,
    Count,
    Divide,
    Expr,
    Max,
    MetricCategory,
    Min,
    Multiply,
    Not,
    Or,
    PeriodAggregate,
    PeriodBasis,
    PeriodGrouping,
    Subtract,
    Sum,
    TransactionSelector,
    TransactionSet,
    infer_quantity_type,
    validate_bool_expr,
)
from halyk_agent.domain.covenants.models import (
    CovenantPlan,
    RequiredFact,
    RequiredFactSource,
)
from halyk_agent.domain.covenants.quantity import QuantityType, TypedQuantity

# Categories Stage 5F cannot yet supply from ledger classification alone. A plan
# that needs one still compiles — it declares the requirement instead of
# borrowing a merely adjacent category.
DOCUMENT_SOURCED_CATEGORIES: frozenset[MetricCategory] = frozenset(
    {
        MetricCategory.GUARANTEE_LIABILITY,
        MetricCategory.INDEMNITY_LIABILITY,
        MetricCategory.SCHEDULED_PRINCIPAL_REPAYMENT,
    }
)


def negate(comparator: Comparator) -> Comparator:
    """Return the comparator that is true exactly when this one is false."""
    return {
        Comparator.LT: Comparator.GTE,
        Comparator.LTE: Comparator.GT,
        Comparator.GT: Comparator.LTE,
        Comparator.GTE: Comparator.LT,
        Comparator.EQ: Comparator.EQ,
    }[comparator]


def breach_comparator(compliance: Comparator) -> Comparator:
    """A covenant is breached when its compliance comparison fails."""
    return negate(compliance)


def walk_expr(expr: Expr) -> tuple[Expr, ...]:
    """Return ``expr`` and every sub-expression, depth-first."""
    found: list[Expr] = []

    def walk(node: Expr) -> None:
        found.append(node)
        if isinstance(node, (Sum, Count, PeriodAggregate)):
            walk(node.of)
        elif isinstance(node, (Min, Max)):
            for arg in node.args:
                walk(arg)
        elif isinstance(node, (Add, Subtract, Multiply)):
            walk(node.left)
            walk(node.right)
        elif isinstance(node, Divide):
            walk(node.numerator)
            walk(node.denominator)

    walk(expr)
    return tuple(found)


def collect_selectors(expr: Expr) -> tuple[TransactionSelector, ...]:
    """Every distinct transaction selector referenced by an expression."""
    seen: dict[str, TransactionSelector] = {}
    for node in walk_expr(expr):
        if isinstance(node, TransactionSet):
            seen.setdefault(node.selector.model_dump_json(), node.selector)
    return tuple(seen.values())


def plan_selectors(plan: CovenantPlan) -> tuple[TransactionSelector, ...]:
    """Every selector the plan needs across actual, activation and breach."""
    seen: dict[str, TransactionSelector] = {}
    for expr in (
        plan.reported_actual,
        *_bool_expressions(plan.activation_condition),
        *_bool_expressions(plan.breach_condition),
    ):
        for selector in collect_selectors(expr):
            seen.setdefault(selector.model_dump_json(), selector)
    return tuple(seen.values())


def _bool_expressions(expr: BoolExpr) -> tuple[Expr, ...]:
    found: list[Expr] = []

    def walk(node: BoolExpr) -> None:
        if isinstance(node, Compare):
            found.extend((node.left, node.right))
        elif isinstance(node, Not):
            walk(node.of)
        elif isinstance(node, (And, Or)):
            for arg in node.args:
                walk(arg)

    walk(expr)
    return tuple(found)


def _period_grouping_for(expr: Expr, selector: TransactionSelector) -> PeriodGrouping | None:
    for node in walk_expr(expr):
        if isinstance(node, PeriodAggregate):
            for inner in walk_expr(node.of):
                if isinstance(inner, TransactionSet) and inner.selector == selector:
                    return node.grouping
    return None


def _period_basis_for(
    expr: Expr, selector: TransactionSelector, default: PeriodBasis
) -> PeriodBasis:
    for node in walk_expr(expr):
        if isinstance(node, PeriodAggregate):
            for inner in walk_expr(node.of):
                if isinstance(inner, TransactionSet) and inner.selector == selector:
                    return node.basis
    return default


def derive_required_facts(plan: CovenantPlan) -> tuple[RequiredFact, ...]:
    """Declare every fact universe the plan needs, with scope and period basis."""
    expressions = (
        plan.reported_actual,
        *_bool_expressions(plan.activation_condition),
        *_bool_expressions(plan.breach_condition),
    )
    facts: dict[str, RequiredFact] = {}
    for expr in expressions:
        for selector in collect_selectors(expr):
            source = (
                RequiredFactSource.DOCUMENT_DISCLOSURE
                if selector.category in DOCUMENT_SOURCED_CATEGORIES
                or selector.scope is not AccountingScope.BORROWER
                else RequiredFactSource.LEDGER_CLASSIFICATION
            )
            fact = RequiredFact(
                category=selector.category,
                scope=selector.scope,
                basis=_period_basis_for(expr, selector, plan.period_basis),
                grouping=_period_grouping_for(expr, selector) or plan.period_grouping,
                source=source,
                related_party_only=selector.related_party_only,
            )
            facts.setdefault(fact.model_dump_json(), fact)
    return tuple(sorted(facts.values(), key=lambda item: item.model_dump_json()))


def _as_constant(expr: Expr) -> TypedQuantity | None:
    return expr.quantity if isinstance(expr, Constant) else None


def derive_primary_comparison(
    plan: CovenantPlan,
) -> tuple[Comparator, TypedQuantity] | None:
    """Reduce a plan to the legacy triple when — and only when — it truly reduces.

    Requires the breach condition to be a single comparison whose left side is the
    reported actual and whose right side is a literal constant. Compound logic and
    expression-valued thresholds deliberately return ``None`` rather than a
    lossy approximation.
    """
    breach = plan.breach_condition
    if not isinstance(breach, Compare):
        return None
    if breach.left != plan.reported_actual:
        return None
    threshold = _as_constant(breach.right)
    if threshold is None:
        return None
    if threshold.quantity_type is QuantityType.PERCENT:
        threshold = threshold.as_ratio()
    # Stored comparator is the compliance direction, matching historical output.
    return negate(breach.comparator), threshold


def simple_plan(
    *,
    metric: Expr,
    compliance_comparator: Comparator,
    threshold: TypedQuantity,
    activation: BoolExpr | None = None,
    period_basis: PeriodBasis = PeriodBasis.CASH_DATE,
    period_grouping: PeriodGrouping | None = None,
) -> CovenantPlan:
    """Build the degenerate plan for ``metric <cmp> constant``."""
    normalized = (
        threshold.as_ratio() if threshold.quantity_type is QuantityType.PERCENT else threshold
    )
    plan = CovenantPlan(
        reported_actual=metric,
        reported_actual_quantity_type=infer_quantity_type(metric),
        activation_condition=activation or Always(),
        breach_condition=Compare(
            left=metric,
            comparator=breach_comparator(compliance_comparator),
            right=Constant(quantity=normalized),
        ),
        period_basis=period_basis,
        period_grouping=period_grouping,
    )
    validate_bool_expr(plan.activation_condition)
    validate_bool_expr(plan.breach_condition)
    return plan.model_copy(update={"required_facts": derive_required_facts(plan)})


def finalize_plan(plan: CovenantPlan) -> CovenantPlan:
    """Type-check a plan and attach its derived required-fact declarations."""
    validate_bool_expr(plan.activation_condition)
    validate_bool_expr(plan.breach_condition)
    actual_type = infer_quantity_type(plan.reported_actual)
    updated = plan.model_copy(
        update={
            "reported_actual_quantity_type": actual_type,
            "required_facts": derive_required_facts(plan),
        }
    )
    return updated
