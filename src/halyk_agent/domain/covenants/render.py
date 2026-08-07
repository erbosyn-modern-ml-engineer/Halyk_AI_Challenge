"""Deterministic covenant definition renderer."""

from __future__ import annotations

from halyk_agent.domain.covenants.ast import (
    Add,
    Constant,
    Count,
    Divide,
    Expr,
    Max,
    Min,
    Multiply,
    Subtract,
    Sum,
    TransactionSet,
)
from halyk_agent.domain.covenants.models import (
    ActivationCondition,
    Comparator,
    CovenantDefinition,
    PeriodDefinition,
    PeriodKind,
    ScopeDefinition,
)
from halyk_agent.domain.covenants.quantity import QuantityType, TypedQuantity

_COMPARATOR_RENDER = {
    Comparator.LT: "must be less than",
    Comparator.LTE: "must not exceed",
    Comparator.GT: "must exceed",
    Comparator.GTE: "must be at least",
    Comparator.EQ: "must equal",
}


def render_quantity(quantity: TypedQuantity) -> str:
    value = format(quantity.value, "f")
    if quantity.quantity_type is QuantityType.MONEY:
        currency = quantity.currency or "USD"
        return f"{currency} {value}"
    if quantity.quantity_type is QuantityType.RATIO:
        return f"{value}x"
    if quantity.quantity_type is QuantityType.PERCENT:
        return f"{value}%"
    return f"{value} (count)"


def render_expr(expr: Expr) -> str:
    if isinstance(expr, Constant):
        return render_quantity(expr.quantity)
    if isinstance(expr, TransactionSet):
        flags = []
        if expr.selector.related_party_only:
            flags.append("related_party")
        if expr.selector.group_level:
            flags.append("group")
        suffix = f"[{','.join(flags)}]" if flags else ""
        return f"{expr.selector.category.value}{suffix}"
    if isinstance(expr, Sum):
        return f"SUM({render_expr(expr.of)})"
    if isinstance(expr, Count):
        return f"COUNT({render_expr(expr.of)})"
    if isinstance(expr, Min):
        return "MIN(" + ", ".join(render_expr(a) for a in expr.args) + ")"
    if isinstance(expr, Max):
        return "MAX(" + ", ".join(render_expr(a) for a in expr.args) + ")"
    if isinstance(expr, Add):
        return f"({render_expr(expr.left)} + {render_expr(expr.right)})"
    if isinstance(expr, Subtract):
        return f"({render_expr(expr.left)} - {render_expr(expr.right)})"
    if isinstance(expr, Multiply):
        return f"({render_expr(expr.left)} * {render_expr(expr.right)})"
    if isinstance(expr, Divide):
        return f"({render_expr(expr.numerator)} / {render_expr(expr.denominator)})"
    raise TypeError(f"unsupported expr: {type(expr)!r}")


def render_period(period: PeriodDefinition) -> str:
    if period.period_kind is PeriodKind.AS_OF:
        return f"as of {period.as_of_date.isoformat() if period.as_of_date else '?'}"
    if period.period_kind is PeriodKind.FINANCIAL_QUARTER:
        end = period.end_date.isoformat() if period.end_date else "?"
        q = period.quarter or "?"
        return f"FY quarter {q} ending {end}"
    start = period.start_date.isoformat() if period.start_date else "?"
    end = period.end_date.isoformat() if period.end_date else "?"
    return f"{start}..{end}"


def render_scope(scope: ScopeDefinition) -> str:
    return scope.scope_kind.value


def render_activation(condition: ActivationCondition) -> str:
    return (
        f"when {render_expr(condition.metric)} "
        f"{_COMPARATOR_RENDER[condition.comparator]} "
        f"{render_quantity(condition.threshold)}"
    )


def render_covenant_definition(definition: CovenantDefinition) -> str:
    """Canonical semantic rendering (not original legal text)."""
    parts = [
        f"{render_expr(definition.metric)} over {render_period(definition.period)}",
        f"{_COMPARATOR_RENDER[definition.comparator]} {render_quantity(definition.threshold)}",
        f"scope={render_scope(definition.scope)}",
        f"family={definition.family_id}",
    ]
    if definition.activation_condition is not None:
        parts.append(render_activation(definition.activation_condition))
    return " | ".join(parts)
