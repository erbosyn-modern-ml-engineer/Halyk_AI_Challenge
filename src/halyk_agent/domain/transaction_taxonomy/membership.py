"""Semantic selector memberships (primary category + hierarchy, no amount duplication)."""

# ruff: noqa: RUF001

from __future__ import annotations

import re

from halyk_agent.domain.covenants.ast import MetricCategory

# Stage 5D selector categories are statement-line semantics, not a generic
# accounting hierarchy. "Operating Expenses" therefore consumes explicit OPEX
# statement rows and authoritative OPEX reclassifications only.
_OPEX_MEMBERS: frozenset[MetricCategory] = frozenset({MetricCategory.OPEX})

MEMBERSHIP_REASON_HIERARCHY = "METRIC_HIERARCHY_MEMBERSHIP"
MEMBERSHIP_REASON_PRIMARY = "PRIMARY_CATEGORY"
MEMBERSHIP_REASON_OPERATING_TAX = "OPERATING_TAX_IN_OPEX"
MEMBERSHIP_REASON_INCOME_TAX_EXCLUDED = "INCOME_OR_PROFIT_TAX_EXCLUDED_FROM_OPEX"
MEMBERSHIP_REASON_TAX_SUBTYPE_UNPROVEN = "TAX_SUBTYPE_NOT_PROVEN_OPERATING"
MEMBERSHIP_REASON_ONE_TIME_ADD_BACK = "AUTHORITATIVE_ONE_TIME_ADD_BACK"

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


_PROPERTY_RENT_LIKE = re.compile(
    r"\b(?:land|warehouse|premises|office|yard|garage|store|property|ground)\b.{0,40}\b(?:lease|rent)\b|"
    r"\b(?:lease|rent)\b.{0,40}\b(?:land|warehouse|premises|office|yard|garage|store|property|ground)\b",
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
    if primary is MetricCategory.LEASE_PAYMENTS and _PROPERTY_RENT_LIKE.search(description):
        members.append(MetricCategory.RENT)
    if (
        primary is MetricCategory.CAPITAL_ASSET_TRANSFERS_TO_UNRESTRICTED_SUBS
        or primary is MetricCategory.CAPITAL_ASSET_TRANSFER
    ):
        # The transferred item remains part of the period's capital-asset spend
        # denominator while its transfer status controls the covenant numerator.
        members.append(MetricCategory.CAPEX)
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
        elif _INCOME_PROFIT_TAX.search(description or ""):
            reasons.append(MEMBERSHIP_REASON_INCOME_TAX_EXCLUDED)
        else:
            reasons.append(MEMBERSHIP_REASON_TAX_SUBTYPE_UNPROVEN)
    return tuple(reasons)


def category_in_memberships(
    primary: MetricCategory,
    memberships: tuple[MetricCategory, ...],
    target: MetricCategory,
) -> bool:
    _ = primary
    return target in memberships
