"""Deterministic period membership predicates for Stage 5F / Stage 6 prep."""

from __future__ import annotations

from datetime import date

from halyk_agent.domain.covenants.models import BoundaryInclusivity, PeriodDefinition, PeriodKind


def _cmp_bound(value: date, bound: date, *, inclusive: BoundaryInclusivity, side: str) -> bool:
    if side == "start":
        return value >= bound if inclusive is BoundaryInclusivity.INCLUSIVE else value > bound
    return value <= bound if inclusive is BoundaryInclusivity.INCLUSIVE else value < bound


def transaction_in_flow_period(
    txn_date: date,
    period: PeriodDefinition,
) -> bool | None:
    """
    Flow / closed-interval membership.

    Returns None when the period kind does not define a single flow window
    (e.g. pure AS_OF) or required bounds are missing.
    """
    if period.period_kind is PeriodKind.AS_OF:
        return None
    if period.period_kind is PeriodKind.MIXED:
        start = period.flow_start_date or period.start_date
        end = period.flow_end_date or period.end_date
    else:
        start = period.start_date
        end = period.end_date
    if start is None or end is None:
        return None
    return _cmp_bound(
        txn_date, start, inclusive=period.start_inclusive, side="start"
    ) and _cmp_bound(txn_date, end, inclusive=period.end_inclusive, side="end")


def transaction_at_or_before_as_of(txn_date: date, period: PeriodDefinition) -> bool | None:
    """AS_OF / MIXED as-of predicate. Does not collapse MIXED flow+as_of."""
    if period.period_kind not in {PeriodKind.AS_OF, PeriodKind.MIXED}:
        return None
    if period.as_of_date is None:
        return None
    return txn_date <= period.as_of_date


def transaction_matches_financial_quarter(txn_date: date, period: PeriodDefinition) -> bool | None:
    if period.period_kind is not PeriodKind.FINANCIAL_QUARTER:
        return None
    if period.quarter is None:
        return None
    # Calendar quarter unless Stage 5D supplies explicit bounds.
    if period.start_date and period.end_date:
        return transaction_in_flow_period(txn_date, period)
    q = period.quarter
    start_month = 3 * (q - 1) + 1
    return txn_date.month >= start_month and txn_date.month < start_month + 3
