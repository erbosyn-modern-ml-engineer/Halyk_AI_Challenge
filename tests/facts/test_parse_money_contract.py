"""Regression contract for complete Stage 5E monetary token parsing."""

from __future__ import annotations

from decimal import Decimal

import pytest

from halyk_agent.domain.fact_extraction.text_locate import parse_money


@pytest.mark.parametrize(
    ("raw", "value", "currency"),
    [
        ("$300,000", Decimal("300000"), "USD"),
        ("$300000", Decimal("300000"), "USD"),
        ("$1250000", Decimal("1250000"), "USD"),
        ("$12345678", Decimal("12345678"), "USD"),
        ("$21847362.55", Decimal("21847362.55"), "USD"),
        ("USD 300000", Decimal("300000"), "USD"),
        ("300 000 USD", Decimal("300000"), "USD"),
        ("1 234,56 EUR", Decimal("1234.56"), "EUR"),
    ],
)
def test_parse_money_consumes_the_complete_value(raw: str, value: Decimal, currency: str) -> None:
    assert parse_money(raw) == (value, currency)


@pytest.mark.parametrize(
    "raw",
    [
        "$3OO,OOO",
        "$5,OOO,000",
        "$1O,000.00",
        "$300,00",
        "$300,,000",
        "$1,,234,567",
        "$300,,,000",
    ],
)
def test_parse_money_never_publishes_a_shorter_valid_prefix(raw: str) -> None:
    assert parse_money(raw) is None
