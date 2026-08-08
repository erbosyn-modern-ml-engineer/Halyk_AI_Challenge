"""Adversarial materiality OCR / ambiguity safety (Stage 6 pre-flight)."""

# ruff: noqa: RUF001

from __future__ import annotations

from decimal import Decimal

import pytest

from halyk_agent.domain.covenants.models import CovenantModifierKind
from halyk_agent.domain.covenants.modifiers import extract_modifier_matches
from halyk_agent.domain.covenants.parse import (
    has_malformed_threshold_token,
    iter_unique_money_quantities,
    parse_threshold,
    scan_money_quantities,
)
from halyk_agent.domain.covenants.quantity import QuantityType


def _floor(text: str):
    matches = extract_modifier_matches(text)
    return [m for m in matches if m.kind is CovenantModifierKind.MATERIALITY_FLOOR]


def test_valid_ru_single_usd_floor() -> None:
    text = "Разовыми признаются статьи в сумме не менее $300,000.00."
    floors = _floor(text)
    assert len(floors) == 1
    assert floors[0].threshold is not None
    assert floors[0].threshold.quantity_type is QuantityType.MONEY
    assert floors[0].threshold.currency == "USD"
    assert floors[0].threshold.value == Decimal("300000.00")


def test_valid_en_single_eur_floor() -> None:
    text = "One-time items are included only when not less than EUR 250,000."
    floors = _floor(text)
    assert len(floors) == 1
    assert floors[0].threshold is not None
    assert floors[0].threshold.currency == "EUR"
    assert floors[0].threshold.value == Decimal("250000")


def test_valid_kzt_materiality_cue_floor() -> None:
    text = "Порог существенности составляет ₸ 50,000,000."
    floors = _floor(text)
    assert len(floors) == 1
    assert floors[0].threshold is not None
    assert floors[0].threshold.currency == "KZT"
    assert floors[0].threshold.value == Decimal("50000000")


@pytest.mark.parametrize(
    "text",
    [
        "не менее $3OO,OOO.OO",
        "not less than $1O,000.00",
        "threshold USD 5OO,OOO",
        "not less than $300,000junk",
        "не менее $1,00,0.0.0",
        "not less than $1..00",
    ],
)
def test_ocr_corrupted_or_malformed_money_never_truncates(text: str) -> None:
    scan = scan_money_quantities(text)
    assert scan.has_malformed is True
    assert scan.quantities == ()
    # Fail-open regressions: truncated prefix amounts must not be published.
    leaked = {Decimal("3"), Decimal("1"), Decimal("5"), Decimal("300")}
    assert not any(q.value in leaked for q in scan.quantities)
    assert _floor(f"Разовыми статьи {text}; к EBITDA не прибавляются.") == []
    assert _floor(f"One-time add-backs {text}.") == []
    assert has_malformed_threshold_token(text) is True
    # Ordinary threshold path must also refuse the token (status present + not ok).
    threshold = parse_threshold(f"must not exceed {text} for the period")
    assert threshold.status == "malformed"
    assert threshold.quantity is None


@pytest.mark.parametrize(
    "text",
    [
        "not less than $300,000.00;",
        "threshold: USD 300,000.",
        "(€250,000)",
        "KZT 50,000,000",
    ],
)
def test_valid_money_near_prose_punctuation(text: str) -> None:
    scan = scan_money_quantities(text)
    assert scan.has_malformed is False
    assert len(scan.quantities) == 1


def test_two_distinct_floors_are_ambiguous() -> None:
    text = (
        "One-time items are included when not less than $300,000.00 and not less than $500,000.00."
    )
    assert _floor(text) == []


def test_ru_competing_floors_are_ambiguous() -> None:
    text = (
        "Разовыми признаются статьи не менее $300,000, "
        "а для настоящего ковенанта применяется порог $500,000."
    )
    assert _floor(text) == []


def test_repeated_identical_floor_dedupes() -> None:
    text = (
        "The materiality floor is $300,000.00 and this $300,000.00 "
        "threshold applies to the add-back calculation."
    )
    floors = _floor(text)
    assert len(floors) == 1
    assert floors[0].threshold is not None
    assert floors[0].threshold.value == Decimal("300000.00")

    text2 = "The materiality threshold is USD 300,000 and the same threshold is $300,000.00."
    floors2 = _floor(text2)
    assert len(floors2) == 1
    assert floors2[0].threshold is not None
    assert floors2[0].threshold.currency == "USD"
    assert floors2[0].threshold.value == Decimal("300000")


def test_unrelated_nearby_money_is_not_false_ambiguity() -> None:
    text = (
        "The auditor reviewed $5,000,000 of expenses. "
        "One-time items are included only if individually not less than $300,000."
    )
    floors = _floor(text)
    assert len(floors) == 1
    assert floors[0].threshold is not None
    assert floors[0].threshold.value == Decimal("300000")
    assert floors[0].threshold.currency == "USD"


def test_percent_materiality_does_not_mint_money() -> None:
    assert _floor("materiality is 5%") == []
    assert iter_unique_money_quantities("materiality is 5%") == ()


def test_missing_materiality_amount_fails_closed() -> None:
    assert _floor("items above the materiality threshold are included") == []


def test_ordinary_threshold_parsing_still_rejects_ocr_corruption() -> None:
    result = parse_threshold("must not exceed $3OO,OOO.OO for the period")
    assert result.status == "malformed"
