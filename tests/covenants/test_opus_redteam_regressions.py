"""Destructive-audit regressions for threshold token integrity."""

from __future__ import annotations

from decimal import Decimal

import pytest

from halyk_agent.domain.covenants.parse import collect_threshold_candidates, scan_money_quantities
from halyk_agent.domain.covenants.quantity import QuantityType


@pytest.mark.parametrize(
    ("text", "quantity_type", "expected"),
    [
        ("не должен превышать 1,68x", QuantityType.RATIO, Decimal("1.68")),
        ("не более 3,0x", QuantityType.RATIO, Decimal("3.0")),
        ("не более 30,5%", QuantityType.PERCENT, Decimal("30.5")),
    ],
)
def test_comma_decimal_thresholds_are_not_parsed_from_fractional_suffix(
    text: str, quantity_type: QuantityType, expected: Decimal
) -> None:
    candidates = collect_threshold_candidates(text)
    matches = [item for item in candidates if item.quantity.quantity_type is quantity_type]
    assert len(matches) == 1
    assert matches[0].quantity.value == expected


@pytest.mark.parametrize("raw", ["$300,,000", "$1,,234,567", "$300,,,000"])
def test_adjacent_money_separators_fail_closed(raw: str) -> None:
    scan = scan_money_quantities(raw)
    assert scan.has_malformed is True
    assert scan.quantities == ()
