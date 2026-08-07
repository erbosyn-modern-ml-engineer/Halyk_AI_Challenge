"""Semantic selector memberships (primary category + hierarchy, no amount duplication)."""

from __future__ import annotations

from halyk_agent.domain.covenants.ast import MetricCategory

# Operating-expense hierarchy for EBITDA-style Stage 5D "Operating Expenses".
# LEASE stays outside OPEX: P1 formula is explicitly OPEX + LEASE (additive).
# INTEREST/CAPEX/REVENUE/FINANCING remain separate from OPEX.
_OPEX_MEMBERS: frozenset[MetricCategory] = frozenset(
    {
        MetricCategory.OPEX,
        MetricCategory.LABOR,
        MetricCategory.UTILITIES,
        MetricCategory.INSURANCE_PREMIUMS,
        MetricCategory.RENT,
        MetricCategory.TAXES,
    }
)

MEMBERSHIP_REASON_HIERARCHY = "METRIC_HIERARCHY_MEMBERSHIP"
MEMBERSHIP_REASON_PRIMARY = "PRIMARY_CATEGORY"


def selector_memberships(primary: MetricCategory) -> tuple[MetricCategory, ...]:
    """Return primary category plus any broader selector memberships."""
    members: list[MetricCategory] = [primary]
    if primary in _OPEX_MEMBERS and primary is not MetricCategory.OPEX:
        members.append(MetricCategory.OPEX)
    # Preserve stable order: primary first, then extras sorted.
    extras = sorted({m for m in members[1:]}, key=lambda m: m.value)
    return (primary, *extras)


def membership_reasons(primary: MetricCategory) -> tuple[str, ...]:
    reasons = [MEMBERSHIP_REASON_PRIMARY]
    if primary in _OPEX_MEMBERS and primary is not MetricCategory.OPEX:
        reasons.append(MEMBERSHIP_REASON_HIERARCHY)
    return tuple(reasons)


def category_in_memberships(
    primary: MetricCategory,
    memberships: tuple[MetricCategory, ...],
    target: MetricCategory,
) -> bool:
    _ = primary
    return target in memberships
