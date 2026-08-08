"""Submission formatting must not inherit process-global Decimal state."""

from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_UP, Decimal, localcontext

import pytest

from halyk_agent.solver.submission.final import _competition_actual


@pytest.mark.parametrize("precision", [3, 6, 9, 28])
@pytest.mark.parametrize("rounding", [ROUND_UP, ROUND_DOWN])
def test_competition_actual_ignores_ambient_decimal_context(
    precision: int,
    rounding: str,
) -> None:
    value = Decimal("21847362.555")
    with localcontext() as ambient:
        ambient.prec = precision
        ambient.rounding = rounding
        actual = _competition_actual(value)
    assert actual == Decimal("21847362.56")


def test_competition_actual_preserves_half_up_boundary() -> None:
    assert _competition_actual(Decimal("1.005")) == Decimal("1.01")
    assert _competition_actual(Decimal("-1.005")) == Decimal("1.01")
