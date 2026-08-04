"""CalculatedValue invariant tests."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from halyk_agent.domain.calculations import CalculatedValue, CalculationTrace


def test_calculated_value_rejects_float() -> None:
    trace = CalculationTrace(
        operation="sum",
        formula="sum(amounts)",
        algorithm_version="1.0.0",
        included_record_ids=["txn-1"],
        excluded_records={},
        parameters={},
    )
    with pytest.raises((ValidationError, TypeError)):
        CalculatedValue(
            id="calc-1",
            value=1.23,  # type: ignore[arg-type]
            currency="KZT",
            trace=trace,
        )


def test_calculated_value_accepts_decimal() -> None:
    trace = CalculationTrace(
        operation="sum",
        formula="sum(amounts)",
        algorithm_version="1.0.0",
        included_record_ids=["txn-1"],
        excluded_records={"txn-2": "reversed"},
        parameters={"scale": "2"},
    )
    value = CalculatedValue(
        id="calc-1",
        value=Decimal("100.00"),
        currency="kzt",
        trace=trace,
    )
    assert value.value == Decimal("100.00")
    assert value.currency == "KZT"
    assert value.trace.excluded_records["txn-2"] == "reversed"
