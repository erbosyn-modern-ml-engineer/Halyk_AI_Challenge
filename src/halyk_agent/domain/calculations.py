"""Deterministic calculation domain models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from halyk_agent.domain.common import CurrencyCode, JsonObject, NonEmptyStr
from halyk_agent.domain.transactions import ExactDecimal, reject_float_amount


class ExcludedRecord(BaseModel):
    """A record excluded from a calculation with an explicit reason."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    record_id: NonEmptyStr
    reason: NonEmptyStr


class CalculationTrace(BaseModel):
    """Deterministic audit trail for a calculated monetary value."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: NonEmptyStr
    formula: NonEmptyStr
    algorithm_version: NonEmptyStr
    included_record_ids: list[NonEmptyStr] = Field(default_factory=list)
    excluded_records: dict[NonEmptyStr, NonEmptyStr] = Field(default_factory=dict)
    parameters: JsonObject = Field(default_factory=dict)


class CalculatedValue(BaseModel):
    """A calculated monetary value with full calculation provenance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: NonEmptyStr
    value: ExactDecimal
    currency: CurrencyCode
    trace: CalculationTrace

    @field_validator("value", mode="before")
    @classmethod
    def _reject_float(cls, value: Any) -> Any:
        return reject_float_amount(value)
