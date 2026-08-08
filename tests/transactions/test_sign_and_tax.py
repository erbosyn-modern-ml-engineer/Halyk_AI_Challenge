"""Stage 5F.2 sign contract and tax/OPEX membership tests."""

from __future__ import annotations

from decimal import Decimal

from halyk_agent.domain.covenants.ast import MetricCategory
from halyk_agent.domain.transaction_taxonomy.amounts import metric_amount_from_source
from halyk_agent.domain.transaction_taxonomy.classify import classify_description
from halyk_agent.domain.transaction_taxonomy.membership import selector_memberships
from halyk_agent.domain.transaction_taxonomy.related_party import possible_damaged_identity_match


def test_expense_and_credit_sign_conversion() -> None:
    assert metric_amount_from_source(Decimal("-100"), category=MetricCategory.UTILITIES) == Decimal(
        "100"
    )
    assert metric_amount_from_source(Decimal("20"), category=MetricCategory.UTILITIES) == Decimal(
        "-20"
    )


def test_revenue_and_financing_sign_as_is() -> None:
    assert metric_amount_from_source(Decimal("100"), category=MetricCategory.REVENUE) == Decimal(
        "100"
    )
    assert metric_amount_from_source(
        Decimal("100"), category=MetricCategory.FINANCING_INFLOWS
    ) == Decimal("100")


def test_fact_derived_positive_magnitude() -> None:
    assert metric_amount_from_source(
        Decimal("100"),
        category=MetricCategory.SEVERANCE_LIABILITY,
        positive_magnitude=True,
    ) == Decimal("100")


def test_ledger_attached_one_time_add_back_uses_expense_negate() -> None:
    # Ledger outflow attached as ONE_TIME must contribute a positive add-back.
    assert metric_amount_from_source(
        Decimal("-251338.94"), category=MetricCategory.ONE_TIME_ADD_BACKS
    ) == Decimal("251338.94")


def test_interest_income_not_financing() -> None:
    hit = classify_description("Interest income on treasury bills")
    assert hit.category is MetricCategory.NON_OPERATING_INCOME


def test_interest_rebate_is_interest_expense_reversal() -> None:
    hit = classify_description("Interest rebate on term loan true-up")
    assert hit.category is MetricCategory.INTEREST_EXPENSE


def test_corporate_income_tax_not_opex() -> None:
    from halyk_agent.domain.transaction_taxonomy.membership import (
        MEMBERSHIP_REASON_INCOME_TAX_EXCLUDED,
        membership_reasons,
    )

    members = selector_memberships(
        MetricCategory.TAXES, description="Corporate income tax instalment"
    )
    assert MetricCategory.TAXES in members
    assert MetricCategory.OPEX not in members
    assert MEMBERSHIP_REASON_INCOME_TAX_EXCLUDED in membership_reasons(
        MetricCategory.TAXES, description="Corporate income tax instalment"
    )


def test_advance_profit_tax_not_opex() -> None:
    members = selector_memberships(
        MetricCategory.TAXES, description="Advance profit tax remittance"
    )
    assert MetricCategory.OPEX not in members


def test_property_and_mineral_tax_in_opex() -> None:
    assert MetricCategory.OPEX in selector_memberships(
        MetricCategory.TAXES, description="Property tax assessment Q2"
    )
    assert MetricCategory.OPEX in selector_memberships(
        MetricCategory.TAXES, description="Mineral extraction tax remittance"
    )


def test_damaged_token_wildcard_only() -> None:
    assert possible_damaged_identity_match("? Holding Group LLP", "Taraz Holding Group LLP")
    assert not possible_damaged_identity_match(
        "? Holding Group LLP", "Kingsley Insurance Associates Holding"
    )


def test_financing_drawdown_still_financing() -> None:
    hit = classify_description("Loan facility drawdown — tranche A")
    assert hit.category is MetricCategory.FINANCING_INFLOWS


def test_reclass_sign_follows_effective_expense_category() -> None:
    # After OPEX-family → INSURANCE, expense negate still applies (no abs()).
    assert metric_amount_from_source(
        Decimal("-100"), category=MetricCategory.INSURANCE_PREMIUMS
    ) == Decimal("100")
    assert metric_amount_from_source(
        Decimal("20"), category=MetricCategory.INSURANCE_PREMIUMS
    ) == Decimal("-20")
