"""Deterministic classifier adversarial tests."""

from __future__ import annotations

from halyk_agent.domain.covenants.ast import MetricCategory
from halyk_agent.domain.transaction_taxonomy.classify import classify_description


def test_strong_category_beats_misleading_keyword() -> None:
    hit = classify_description("Plant and boiler insurance premium — Taraz site 2025")
    assert hit.status == "CLASSIFIED"
    assert hit.category is MetricCategory.INSURANCE_PREMIUMS


def test_capitalised_interest_is_interest_cost_not_capex() -> None:
    hit = classify_description("Capitalised interest charge — Q4 2025")
    assert hit.status == "CLASSIFIED"
    assert hit.category is MetricCategory.INTEREST_EXPENSE


def test_rent_credit_is_not_customer_revenue() -> None:
    hit = classify_description("Sublet rent received — February")
    assert hit.status == "CLASSIFIED"
    assert hit.category is MetricCategory.RENT
    assert hit.category is not MetricCategory.REVENUE


def test_genuine_customer_revenue() -> None:
    hit = classify_description("Customer invoice collection — March settlement")
    assert hit.status == "CLASSIFIED"
    assert hit.category is MetricCategory.REVENUE


def test_ad_campaign_is_generic_expense_not_statement_opex() -> None:
    hit = classify_description("Product launch ad campaign — Q1")
    assert hit.status == "CLASSIFIED"
    assert hit.category is MetricCategory.OTHER_EXPENSE


def test_direct_industrial_servicing_is_statement_opex() -> None:
    hit = classify_description("Catalyst regeneration servicing contract")
    assert hit.status == "CLASSIFIED"
    assert hit.category is MetricCategory.OPEX


def test_generic_consulting_is_other_expense_not_statement_opex() -> None:
    hit = classify_description("Freight arbitration consulting settlement")
    assert hit.status == "CLASSIFIED"
    assert hit.category is MetricCategory.OTHER_EXPENSE


def test_payroll_rebate_is_labor_credit() -> None:
    hit = classify_description("Payroll agency rebate — hub 2025")
    assert hit.status == "CLASSIFIED"
    assert hit.category is MetricCategory.LABOR


def test_telecom_leased_line_is_utility_not_property_lease() -> None:
    hit = classify_description("Telecom leased line — site office")
    assert hit.status == "CLASSIFIED"
    assert hit.category is MetricCategory.UTILITIES


def test_lease_vs_rent_collision_when_both_expense() -> None:
    hit = classify_description("Office rent and equipment lease combined charge")
    assert hit.status == "CONFLICT"
    assert "LEASE" in hit.competing and "RENT" in hit.competing


def test_unknown_description_unresolved() -> None:
    hit = classify_description("Miscellaneous unclassified internal memo")
    assert hit.status == "UNRESOLVED"
    assert hit.category is None
