"""Semantic selector memberships (primary category + hierarchy, no amount duplication)."""

# ruff: noqa: RUF001

from __future__ import annotations

import re

from halyk_agent.domain.covenants.ast import MetricCategory

# Operating-expense hierarchy for EBITDA-style Stage 5D "Operating Expenses".
# LEASE stays outside OPEX: P1 formula is explicitly OPEX + LEASE (additive).
# INTEREST/CAPEX/REVENUE/FINANCING remain separate from OPEX.
# TAXES: OPEX membership is row-level (income/profit tax excluded).
_OPEX_MEMBERS: frozenset[MetricCategory] = frozenset(
    {
        MetricCategory.OPEX,
        MetricCategory.LABOR,
        MetricCategory.UTILITIES,
        MetricCategory.INSURANCE_PREMIUMS,
        MetricCategory.RENT,
    }
)

MEMBERSHIP_REASON_HIERARCHY = "METRIC_HIERARCHY_MEMBERSHIP"
MEMBERSHIP_REASON_PRIMARY = "PRIMARY_CATEGORY"
MEMBERSHIP_REASON_OPERATING_TAX = "OPERATING_TAX_IN_OPEX"
MEMBERSHIP_REASON_INCOME_TAX_EXCLUDED = "INCOME_TAX_EXCLUDED_FROM_OPEX"

_INCOME_PROFIT_TAX = re.compile(
    r"\b(?:corporate\s+)?(?:income|profit)\s+tax\b|"
    r"\badvance\s+(?:corporate\s+)?(?:income|profit)\s+tax\b|"
    r"\bналог\s+на\s+прибыль\b",
    re.IGNORECASE,
)

_OPERATING_TAX = re.compile(
    r"\b(?:payroll|social|property|land|vehicle|municipal|environmental|"
    r"emissions|excise|customs|mineral\s+extraction|use|franchise|"
    r"withholding|VAT)\s+tax\b|"
    r"\bcustoms\s+duty\b|"
    r"\bVAT\b",
    re.IGNORECASE,
)


def tax_has_opex_membership(text: str) -> bool:
    """Return True when a TAXES row should also match OPEX selectors."""
    if _INCOME_PROFIT_TAX.search(text or ""):
        return False
    # Unknown tax subtype: fail closed — keep TAXES primary, no OPEX membership.
    return bool(_OPERATING_TAX.search(text or ""))


def selector_memberships(
    primary: MetricCategory,
    *,
    description: str = "",
) -> tuple[MetricCategory, ...]:
    """Return primary category plus any broader selector memberships."""
    members: list[MetricCategory] = [primary]
    if primary in _OPEX_MEMBERS and primary is not MetricCategory.OPEX:
        members.append(MetricCategory.OPEX)
    if primary is MetricCategory.TAXES and tax_has_opex_membership(description):
        members.append(MetricCategory.OPEX)
    if (
        primary is MetricCategory.CAPITAL_ASSET_TRANSFERS_TO_UNRESTRICTED_SUBS
        or primary is MetricCategory.CAPITAL_ASSET_TRANSFER
    ):
        # Unrestricted-sub selector matches only via unrestricted category + status.
        pass
    extras = sorted({m for m in members[1:]}, key=lambda m: m.value)
    return (primary, *extras)


def membership_reasons(
    primary: MetricCategory,
    *,
    description: str = "",
) -> tuple[str, ...]:
    reasons = [MEMBERSHIP_REASON_PRIMARY]
    if primary in _OPEX_MEMBERS and primary is not MetricCategory.OPEX:
        reasons.append(MEMBERSHIP_REASON_HIERARCHY)
    if primary is MetricCategory.TAXES:
        if tax_has_opex_membership(description):
            reasons.append(MEMBERSHIP_REASON_OPERATING_TAX)
        else:
            reasons.append(MEMBERSHIP_REASON_INCOME_TAX_EXCLUDED)
    return tuple(reasons)


def category_in_memberships(
    primary: MetricCategory,
    memberships: tuple[MetricCategory, ...],
    target: MetricCategory,
) -> bool:
    _ = primary
    return target in memberships
