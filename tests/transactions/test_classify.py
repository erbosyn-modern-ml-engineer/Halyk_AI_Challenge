"""Deterministic classifier adversarial tests."""

from __future__ import annotations

from halyk_agent.domain.covenants.ast import MetricCategory
from halyk_agent.domain.transaction_taxonomy.classify import classify_description


def test_strong_category_beats_misleading_keyword() -> None:
    hit = classify_description("Plant and boiler insurance premium — Taraz site 2025")
    assert hit.status == "CLASSIFIED"
    assert hit.category is MetricCategory.INSURANCE_PREMIUMS


def test_capitalised_interest_is_capex_not_conflict() -> None:
    hit = classify_description("Capitalised interest charge — Q4 2025")
    assert hit.status == "CLASSIFIED"
    assert hit.category is MetricCategory.CAPEX


def test_rent_inflow_is_revenue_not_rent_expense() -> None:
    hit = classify_description("Sublet rent received — February")
    assert hit.status == "CLASSIFIED"
    assert hit.category is MetricCategory.REVENUE


def test_lease_vs_rent_collision_when_both_expense() -> None:
    hit = classify_description("Office rent and equipment lease combined charge")
    assert hit.status == "CONFLICT"
    assert "LEASE" in hit.competing and "RENT" in hit.competing


def test_unknown_description_unresolved() -> None:
    hit = classify_description("Miscellaneous unclassified internal memo")
    assert hit.status == "UNRESOLVED"
    assert hit.category is None
