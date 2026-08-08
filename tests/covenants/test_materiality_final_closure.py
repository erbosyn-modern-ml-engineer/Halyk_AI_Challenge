"""Final Stage 6 pre-flight materiality root-closure adversarial tests."""

# ruff: noqa: RUF001

from __future__ import annotations

from decimal import Decimal

import pytest

from halyk_agent.domain.covenants.engine import _typed_materiality_from_document
from halyk_agent.domain.covenants.models import CovenantModifierKind
from halyk_agent.domain.covenants.modifiers import extract_modifier_matches
from halyk_agent.domain.covenants.parse import (
    has_malformed_threshold_token,
    parse_threshold,
    scan_money_quantities,
)
from halyk_agent.domain.covenants.quantity import QuantityType
from tests.authority.helpers import make_document


def _floors(text: str):
    return [
        m
        for m in extract_modifier_matches(text)
        if m.kind is CovenantModifierKind.MATERIALITY_FLOOR
    ]


def _assert_no_floor(text: str) -> None:
    floors = _floors(text)
    assert floors == []
    # Guard against a future API that returns empty threshold objects.
    assert not any(m.threshold is not None for m in floors)


def _assert_single_floor(text: str, *, currency: str, value: Decimal) -> None:
    floors = _floors(text)
    assert len(floors) == 1
    threshold = floors[0].threshold
    assert threshold is not None
    assert threshold.quantity_type is QuantityType.MONEY
    assert threshold.currency == currency
    assert threshold.value == value
    # Wrong truncated / competitor values must be absent.
    assert threshold.value not in {Decimal("3"), Decimal("1"), Decimal("5"), Decimal("300")}


# ---------------------------------------------------------------------------
# HIGH-3 — complete money token grammar
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "currency", "value"),
    [
        ("$300 000", "USD", Decimal("300000")),
        ("₸ 50 000 000", "KZT", Decimal("50000000")),
        ("USD 1 250 000.50", "USD", Decimal("1250000.50")),
        ("$300\xa0000", "USD", Decimal("300000")),
        ("$300\u202f000", "USD", Decimal("300000")),
        ("$300\u2009000", "USD", Decimal("300000")),
        ("$300,000.00", "USD", Decimal("300000.00")),
        ("EUR 250,000", "EUR", Decimal("250000")),
    ],
)
def test_complete_money_token_accepts_grouped_forms(
    text: str, currency: str, value: Decimal
) -> None:
    scan = scan_money_quantities(text)
    assert scan.has_malformed is False
    assert len(scan.quantities) == 1
    assert scan.quantities[0].currency == currency
    assert scan.quantities[0].value == value
    assert scan.quantities[0].value not in {Decimal("3"), Decimal("50"), Decimal("300")}


@pytest.mark.parametrize(
    "text",
    [
        "$3-00,000",
        "$30 00",
        "$3 00 000",
        "$300 00",
        "$1,00,0.0.0",
        "$1..00",
        "$3OO,OOO.OO",
        "$30О,000",
        "$300,000junk",
        "$300 000junk",
        "$300'000",
        "$3O0,000",
        "$3О0,000",
        "$3Ο0,000",
        "USD 5ОО,ООО",
    ],
)
def test_complete_money_token_rejects_malformed_without_prefix_truncation(text: str) -> None:
    scan = scan_money_quantities(text)
    assert scan.has_malformed is True
    assert scan.quantities == ()
    assert has_malformed_threshold_token(text) is True
    # Explicit anti-truncation: never publish short prefix amounts.
    assert all(
        q.value
        not in {
            Decimal("1"),
            Decimal("3"),
            Decimal("5"),
            Decimal("30"),
            Decimal("300"),
            Decimal("300000"),
        }
        for q in scan.quantities
    )


def test_money_range_discovers_both_endpoints_not_prefix() -> None:
    scan = scan_money_quantities("$300,000-$500,000")
    assert scan.has_malformed is False
    assert {(q.currency, q.value) for q in scan.quantities} == {
        ("USD", Decimal("300000")),
        ("USD", Decimal("500000")),
    }
    assert all(q.value != Decimal("300") for q in scan.quantities)
    assert _floors("One-time items not less than $300,000-$500,000.") == []


def test_malformed_and_valid_money_in_same_region_fail_closed() -> None:
    text = "Разовыми признаются статьи не менее $3OO,OOO и не менее $300,000."
    scan = scan_money_quantities(text)
    assert scan.has_malformed is True
    _assert_no_floor(text)
    reverse = "Разовыми признаются статьи не менее $300,000 и не менее $3OO,OOO."
    assert scan_money_quantities(reverse).has_malformed is True
    _assert_no_floor(reverse)


# ---------------------------------------------------------------------------
# HIGH-1 — sentence-crossing / legal abbreviations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Разовыми признаются статьи согласно п. 5 не менее $300,000 и не менее $500,000.",
        "Разовыми признаются статьи согласно ст. 7 не менее $300,000 и не менее $500,000.",
        (
            "One-time items under cl. 4 are included if not less than "
            "$300,000 and not less than $500,000."
        ),
        (
            "One-time items under Sec. 3 are included if not less than "
            "$300,000 and not less than $500,000."
        ),
        (
            "One-time items are included when not less than $300,000. "
            "One-time add-backs below materiality also require not less than $500,000."
        ),
    ],
)
def test_sentence_crossing_and_abbrev_regions_are_ambiguous(text: str) -> None:
    floors = _floors(text)
    assert floors == []
    # Must not first-match-win on USD 300000.
    assert all(m.threshold is None or m.threshold.value != Decimal("300000") for m in floors)


def test_distractor_money_outside_instruction_still_isolated() -> None:
    text = (
        "The auditor reviewed $5,000,000 of expenses. "
        "One-time items are included if not less than $300,000."
    )
    _assert_single_floor(text, currency="USD", value=Decimal("300000"))


# ---------------------------------------------------------------------------
# HIGH-2 — multi-instruction document reconciliation
# ---------------------------------------------------------------------------


def test_single_materiality_instruction_publishes_floor() -> None:
    text = "One-time items are included when not less than $300,000."
    _assert_single_floor(text, currency="USD", value=Decimal("300000"))


@pytest.mark.parametrize(
    "text",
    [
        (
            "One-time items are included when not less than $300,000.\n\n"
            "Amendment: one-time add-backs are included when not less than $500,000."
        ),
        (
            "One-time items are included when not less than $500,000.\n\n"
            "Amendment: one-time add-backs are included when not less than $300,000."
        ),
        (
            "One-time floor is not less than $300,000.\n\n"
            "Later amendment raises the one-time materiality floor to not less than $500,000."
        ),
    ],
)
def test_competing_document_instructions_fail_closed_order_independent(text: str) -> None:
    floors = _floors(text)
    assert floors == []
    assert all(
        m.threshold is None or m.threshold.value not in {Decimal("300000"), Decimal("500000")}
        for m in floors
    )


def test_repeated_equivalent_document_floors_dedupe() -> None:
    text = (
        "One-time items are included when not less than $300,000.\n\n"
        "Amendment: one-time add-backs are included when not less than USD 300000.00."
    )
    _assert_single_floor(text, currency="USD", value=Decimal("300000"))


@pytest.mark.parametrize(
    "text",
    [
        (
            "One-time items are included when not less than $300,000.\n\n"
            "Amendment: one-time add-backs are included when not less than $5OO,OOO."
        ),
        (
            "One-time items are included when not less than $5OO,OOO.\n\n"
            "Amendment: one-time add-backs are included when not less than $300,000."
        ),
    ],
)
def test_valid_and_malformed_competing_instructions_fail_closed(text: str) -> None:
    _assert_no_floor(text)


def test_neighboring_non_materiality_money_does_not_compete() -> None:
    text = (
        "One-time items are included when not less than $300,000. "
        "Separately, capital expenditure must not exceed $500,000."
    )
    _assert_single_floor(text, currency="USD", value=Decimal("300000"))


def test_typed_materiality_from_document_reconciles_all_instructions() -> None:
    """Production entry path used by FA attachment — not only extract_modifier_matches."""
    conflict = (
        "One-time items are included when not less than $300,000.\n\n"
        "Amendment: one-time add-backs are included when not less than $500,000."
    )
    modifier, spans = _typed_materiality_from_document(
        make_document(raw_text=conflict, sha="c" * 64)
    )
    assert modifier is None
    assert spans == ()

    single = "Разовыми признаются статьи в сумме не менее $300,000.00; к EBITDA не прибавляются."
    modifier, spans = _typed_materiality_from_document(make_document(raw_text=single, sha="d" * 64))
    assert modifier is not None
    assert modifier.kind is CovenantModifierKind.MATERIALITY_FLOOR
    assert modifier.threshold is not None
    assert modifier.threshold.currency == "USD"
    assert modifier.threshold.value == Decimal("300000.00")
    assert spans

    malformed_competitor = (
        "One-time items are included when not less than $300,000.\n\n"
        "Amendment: one-time add-backs are included when not less than $5OO,OOO."
    )
    modifier, spans = _typed_materiality_from_document(
        make_document(raw_text=malformed_competitor, sha="e" * 64)
    )
    assert modifier is None
    assert spans == ()


# ---------------------------------------------------------------------------
# Generic threshold / prior safety non-regression
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "currency", "value"),
    [
        ("not less than $3,500,000", "USD", Decimal("3500000")),
        ("must not exceed $225,000", "USD", Decimal("225000")),
        ("EUR 250,000", "EUR", Decimal("250000")),
        ("KZT 50,000,000", "KZT", Decimal("50000000")),
        ("USD 1 250 000", "USD", Decimal("1250000")),
    ],
)
def test_generic_threshold_money_non_regression(text: str, currency: str, value: Decimal) -> None:
    result = parse_threshold(text)
    assert result.status == "ok"
    assert result.quantity is not None
    assert result.quantity.quantity_type is QuantityType.MONEY
    assert result.quantity.currency == currency
    assert result.quantity.value == value


@pytest.mark.parametrize("text", ["2.00x", "0.42x", "limit 2.00x", "at least 0.42x"])
def test_ratio_thresholds_unaffected(text: str) -> None:
    result = parse_threshold(text)
    assert result.status == "ok"
    assert result.quantity is not None
    assert result.quantity.quantity_type is QuantityType.RATIO
    assert result.quantity.currency is None


def test_prior_ocr_corruption_still_fail_closed() -> None:
    for token in ("$3OO,OOO.OO", "$1O,000.00", "USD 5OO,OOO"):
        scan = scan_money_quantities(token)
        assert scan.has_malformed is True
        assert scan.quantities == ()
        assert all(
            q.value not in {Decimal("3"), Decimal("1"), Decimal("5")} for q in scan.quantities
        )
