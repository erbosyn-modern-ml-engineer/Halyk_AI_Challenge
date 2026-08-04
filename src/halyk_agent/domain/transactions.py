"""Monetary helpers and transaction models."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    field_validator,
)

from halyk_agent.domain.common import CurrencyCode, JsonObject, NonEmptyStr


def reject_float_amount(value: Any) -> Any:
    """Reject Python float amounts before Decimal conversion."""
    if isinstance(value, float):
        raise TypeError("monetary amounts must not use float; use Decimal, str, or int")
    return value


def coerce_decimal_amount(value: Any) -> Decimal:
    """Coerce supported amount inputs into Decimal without float conversion."""
    value = reject_float_amount(value)
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise TypeError("monetary amounts must not use bool")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            raise ValueError("amount string must be non-empty")
        try:
            return Decimal(stripped)
        except InvalidOperation as exc:
            raise ValueError(f"invalid decimal amount: {value!r}") from exc
    raise TypeError(
        f"unsupported amount type {type(value).__name__}; expected Decimal, str, or int"
    )


def serialize_decimal(value: Decimal) -> str:
    """Serialize Decimal as a string to avoid binary float artifacts."""
    return format(value, "f")


ExactDecimal = Annotated[
    Decimal,
    BeforeValidator(coerce_decimal_amount),
    PlainSerializer(serialize_decimal, return_type=str),
]


class TransactionStatus(StrEnum):
    """Transaction lifecycle status."""

    PENDING = "pending"
    POSTED = "posted"
    SETTLED = "settled"
    REVERSED = "reversed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class Transaction(BaseModel):
    """A financial transaction with Decimal-safe monetary amount."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: NonEmptyStr
    amount: ExactDecimal
    currency: CurrencyCode
    occurred_at: datetime
    status: TransactionStatus
    external_id: NonEmptyStr | None = None
    entity_id: NonEmptyStr | None = None
    counterparty_id: NonEmptyStr | None = None
    contract_id: NonEmptyStr | None = None
    invoice_id: NonEmptyStr | None = None
    posted_at: datetime | None = None
    settled_at: datetime | None = None
    transaction_type: NonEmptyStr | None = None
    reversal_of_id: NonEmptyStr | None = None
    parent_transaction_id: NonEmptyStr | None = None
    description: NonEmptyStr | None = None
    metadata: JsonObject = Field(default_factory=dict)

    @field_validator("amount", mode="before")
    @classmethod
    def _reject_float(cls, value: Any) -> Any:
        return reject_float_amount(value)
