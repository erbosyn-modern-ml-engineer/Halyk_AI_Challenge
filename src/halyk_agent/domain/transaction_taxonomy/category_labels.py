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
    "консультационные услуги": MetricCategory.OPEX,
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
