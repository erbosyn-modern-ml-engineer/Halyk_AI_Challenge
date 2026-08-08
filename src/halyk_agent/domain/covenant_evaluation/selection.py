"""Exact selector and period predicates shared by context validation and execution."""

from __future__ import annotations

from datetime import date

from halyk_agent.domain.covenants.ast import TransactionSelector
from halyk_agent.domain.covenants.models import PeriodDefinition, PeriodKind
from halyk_agent.domain.transaction_taxonomy.models import (
    CalculationInput,
    InputPeriodSemantics,
)
from halyk_agent.domain.transaction_taxonomy.period import (
    transaction_in_flow_period,
    transaction_matches_financial_quarter,
)
from halyk_agent.domain.transaction_taxonomy.selectors import input_matches_selector


def input_matches_selector_strict(
    inp: CalculationInput,
    selector: TransactionSelector,
) -> bool:
    """Stage 5F semantic matcher plus explicit include/exclude flag predicates."""

    if not input_matches_selector(inp, selector):
        return False
    flags = set(inp.flags)
    if any(flag not in flags for flag in selector.include_flags):
        return False
    if any(flag in flags for flag in selector.exclude_flags):
        return False
    return True


def input_period_membership(
    inp: CalculationInput,
    period: PeriodDefinition,
) -> bool | None:
    """Return True/False/None using the frozen FLOW / AS_OF contract.

    ``None`` means the input cannot be placed in the covenant period without
    inventing time semantics and therefore must propagate UNRESOLVED.
    """

    if inp.period_excluded:
        return False

    def flow_membership(value: date) -> bool | None:
        if period.period_kind is PeriodKind.FINANCIAL_QUARTER:
            return transaction_matches_financial_quarter(value, period)
        return transaction_in_flow_period(value, period)

    if inp.period_semantics is InputPeriodSemantics.FLOW:
        if inp.transaction_date is not None:
            return flow_membership(inp.transaction_date)

        if inp.period_start is None or inp.period_end is None:
            return None
        start_ok = flow_membership(inp.period_start)
        end_ok = flow_membership(inp.period_end)
        if start_ok is None or end_ok is None:
            return None
        return bool(start_ok and end_ok)

    if inp.period_semantics is InputPeriodSemantics.AS_OF:
        if period.as_of_date is None or inp.as_of_date is None:
            return None
        # A stock measurement is source-backed for one exact instant.  Using an
        # older snapshot as if it were current would silently invent continuity.
        return inp.as_of_date == period.as_of_date

    return None
