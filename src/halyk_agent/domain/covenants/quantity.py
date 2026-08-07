"""Typed quantity primitives for Stage 5D (definitions, not ledger actuals)."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from halyk_agent.domain.common import NonEmptyStr
from halyk_agent.domain.transactions import ExactDecimal, coerce_decimal_amount, reject_float_amount


class QuantityType(StrEnum):
    """
    Semantic quantity kinds used by covenant definitions.

    PERCENT is a distinct presentation type (value is percentage points, e.g. 5 means 5%).
    RATIO is a unitless quotient (e.g. 0.42x). Do not attach currency to RATIO/PERCENT/COUNT.
    MONEY is a currency amount without embedding FX conversion here.
    """

    MONEY = "MONEY"
    RATIO = "RATIO"
    PERCENT = "PERCENT"
    COUNT = "COUNT"


class TypedQuantity(BaseModel):
    """Immutable typed numeric constant (Decimal only; floats rejected)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    quantity_type: QuantityType
    value: ExactDecimal
    currency: NonEmptyStr | None = None

    @field_validator("value", mode="before")
    @classmethod
    def _no_float(cls, value: Any) -> Any:
        return reject_float_amount(value)

    @model_validator(mode="after")
    def _currency_rules(self) -> TypedQuantity:
        if self.quantity_type is QuantityType.MONEY:
            if self.currency is None:
                raise ValueError("MONEY quantity requires currency")
        elif self.currency is not None:
            raise ValueError(f"{self.quantity_type.value} must not carry currency")
        return self

    def as_ratio(self) -> TypedQuantity:
        """Normalize PERCENT → RATIO for algebraic comparison; RATIO unchanged."""
        if self.quantity_type is QuantityType.RATIO:
            return self
        if self.quantity_type is QuantityType.PERCENT:
            return TypedQuantity(
                quantity_type=QuantityType.RATIO,
                value=coerce_decimal_amount(self.value) / Decimal("100"),
            )
        raise ValueError(f"cannot convert {self.quantity_type.value} to RATIO")


class CovenantTypeError(ValueError):
    """Structured semantic type failure for covenant expressions."""

    def __init__(self, message: str, *, code: str = "COVENANT_TYPE_ERROR") -> None:
        super().__init__(message)
        self.message = message
        self.code = code
