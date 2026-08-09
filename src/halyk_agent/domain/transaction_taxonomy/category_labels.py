"""Map free-text / Stage 5E category labels onto MetricCategory."""

from __future__ import annotations

from halyk_agent.domain.covenants.ast import MetricCategory

# Source-faithful aliases observed in Stage 5E payloads + English/Russian labels.
_LABEL_TO_CATEGORY: dict[str, MetricCategory] = {
    "revenue": MetricCategory.REVENUE,
    "выручка": MetricCategory.REVENUE,
    "capex": MetricCategory.CAPEX,
    "капитальн": MetricCategory.CAPEX,
    "opex": MetricCategory.OPEX,
    "operating expenses": MetricCategory.OPEX,
    "операционные расходы": MetricCategory.OPEX,
    # Consulting/Marketing stay OTHER_EXPENSE until dedicated MetricCategory exists;
    # taxonomy engine stamps CONSULTING/MARKETING flags from these labels.
    "консультационные услуги": MetricCategory.OTHER_EXPENSE,
    "consulting": MetricCategory.OTHER_EXPENSE,
    "consulting expenses": MetricCategory.OTHER_EXPENSE,
    "consulting fees": MetricCategory.OTHER_EXPENSE,
    "маркетинг": MetricCategory.OTHER_EXPENSE,
    "маркетинговые расходы": MetricCategory.OTHER_EXPENSE,
    "marketing": MetricCategory.OTHER_EXPENSE,
    "marketing expenses": MetricCategory.OTHER_EXPENSE,
    "marketing expense": MetricCategory.OTHER_EXPENSE,
    "scheduled principal": MetricCategory.OTHER_EXPENSE,
    "principal repayment": MetricCategory.OTHER_EXPENSE,
    "interest expense": MetricCategory.INTEREST_EXPENSE,
    "interest_expense": MetricCategory.INTEREST_EXPENSE,
    "процентные расходы": MetricCategory.INTEREST_EXPENSE,
    "lease payments": MetricCategory.LEASE_PAYMENTS,
    "lease_payments": MetricCategory.LEASE_PAYMENTS,
    "related party payments": MetricCategory.RELATED_PARTY_PAYMENTS,
    "related_party_payments": MetricCategory.RELATED_PARTY_PAYMENTS,
    "labor": MetricCategory.LABOR,
    "utilities": MetricCategory.UTILITIES,
    "taxes": MetricCategory.TAXES,
    "insurance premiums": MetricCategory.INSURANCE_PREMIUMS,
    "insurance_premiums": MetricCategory.INSURANCE_PREMIUMS,
    "страховые премии": MetricCategory.INSURANCE_PREMIUMS,
    "rent": MetricCategory.RENT,
    "financing inflows": MetricCategory.FINANCING_INFLOWS,
    "financing_inflows": MetricCategory.FINANCING_INFLOWS,
    "severance liability": MetricCategory.SEVERANCE_LIABILITY,
    "severance_liability": MetricCategory.SEVERANCE_LIABILITY,
    "group capex": MetricCategory.GROUP_CAPEX,
    "group_capex": MetricCategory.GROUP_CAPEX,
    "one time add backs": MetricCategory.ONE_TIME_ADD_BACKS,
    "one_time_add_backs": MetricCategory.ONE_TIME_ADD_BACKS,
    "capital asset transfers to unrestricted subs": (
        MetricCategory.CAPITAL_ASSET_TRANSFERS_TO_UNRESTRICTED_SUBS
    ),
}


def map_category_label(label: str | None) -> MetricCategory | None:
    """Map a human/fact category label to MetricCategory; None if unknown."""
    if label is None:
        return None
    raw = label.strip()
    if not raw:
        return None
    # Exact enum value
    try:
        return MetricCategory(raw)
    except ValueError:
        pass
    folded = " ".join(raw.casefold().split())
    if folded in _LABEL_TO_CATEGORY:
        return _LABEL_TO_CATEGORY[folded]
    # Prefix / containment for Russian stems already listed with spaces normalized
    for key, category in _LABEL_TO_CATEGORY.items():
        if key in folded or folded in key:
            return category
    return None


def expense_flag_from_label(label: str | None) -> str | None:
    """Return MARKETING / CONSULTING / SCHEDULED_PRINCIPAL overlay flag from a label."""
    if label is None:
        return None
    folded = " ".join(label.casefold().split())
    if not folded:
        return None
    if any(
        token in folded
        for token in (
            "marketing",
            "маркетинг",
            "advertis",
            "sponsorship",
            "media buy",
        )
    ):
        return "MARKETING"
    if any(
        token in folded
        for token in (
            "consulting",
            "консульт",
            "advisory",
            "professional fee",
            "professional service",
        )
    ):
        return "CONSULTING"
    if any(
        token in folded
        for token in (
            "scheduled principal",
            "principal repayment",
            "основного долга",
        )
    ):
        return "SCHEDULED_PRINCIPAL"
    return None


def expense_flag_from_rule(rule: str | None) -> str | None:
    """Map classifier rule ids onto selectable expense-flag overlays."""
    if not rule:
        return None
    upper = rule.upper()
    if "MARKETING" in upper:
        return "MARKETING"
    if "CONSULTING" in upper:
        return "CONSULTING"
    if "SCHEDULED_PRINCIPAL" in upper or upper == "PRINCIPAL_REPAYMENT":
        return "SCHEDULED_PRINCIPAL"
    return None
