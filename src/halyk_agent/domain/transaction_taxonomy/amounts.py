"""Metric-amount / sign contract for Stage 6 consumption."""

from __future__ import annotations

from decimal import Decimal

from halyk_agent.domain.covenants.ast import MetricCategory
from halyk_agent.domain.transaction_taxonomy.models import AmountSemantics, SignRule

_INFLOW_CATEGORIES = frozenset(
    {
        MetricCategory.REVENUE,
        MetricCategory.FINANCING_INFLOWS,
        MetricCategory.NON_OPERATING_INCOME,
    }
)

_POSITIVE_MAGNITUDE_CATEGORIES = frozenset(
    {
        MetricCategory.SEVERANCE_LIABILITY,
        MetricCategory.GROUP_CAPEX,
    }
)


def sign_contract_for_category(
    category: MetricCategory,
    *,
    positive_magnitude: bool = False,
) -> tuple[AmountSemantics, SignRule]:
    if positive_magnitude or category in _POSITIVE_MAGNITUDE_CATEGORIES:
        return AmountSemantics.POSITIVE_MAGNITUDE, SignRule.POSITIVE_MAGNITUDE_AS_IS
    if category in _INFLOW_CATEGORIES:
        return AmountSemantics.SOURCE_SIGNED_FLOW, SignRule.INFLOW_AS_IS
    return AmountSemantics.SOURCE_SIGNED_FLOW, SignRule.EXPENSE_NEGATE_SOURCE


def metric_amount_from_source(
    source_amount: Decimal,
    *,
    category: MetricCategory,
    positive_magnitude: bool = False,
) -> Decimal:
    semantics, rule = sign_contract_for_category(category, positive_magnitude=positive_magnitude)
    _ = semantics
    if rule is SignRule.POSITIVE_MAGNITUDE_AS_IS:
        return source_amount
    if rule is SignRule.INFLOW_AS_IS:
        return source_amount
    # EXPENSE_NEGATE_SOURCE: expense -100 -> +100; refund +20 -> -20
    return -source_amount
