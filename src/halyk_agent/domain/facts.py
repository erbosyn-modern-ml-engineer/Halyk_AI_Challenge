"""Explicit and derived fact models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from halyk_agent.domain.common import JsonValue, NonEmptyStr
from halyk_agent.domain.evidence import EvidenceSpan


class ExplicitFact(BaseModel):
    """A fact extracted directly from evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: NonEmptyStr
    fact_type: NonEmptyStr
    subject_id: NonEmptyStr
    value: JsonValue
    evidence: list[EvidenceSpan] = Field(min_length=1)

    @field_validator("evidence")
    @classmethod
    def _require_evidence(cls, value: list[EvidenceSpan]) -> list[EvidenceSpan]:
        if not value:
            raise ValueError("ExplicitFact requires at least one EvidenceSpan")
        return value


class DerivedFact(BaseModel):
    """A fact derived from other facts without requiring direct document spans."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: NonEmptyStr
    fact_type: NonEmptyStr
    subject_id: NonEmptyStr
    value: JsonValue
    input_fact_ids: list[NonEmptyStr] = Field(min_length=1)
    derivation: NonEmptyStr

    @field_validator("input_fact_ids")
    @classmethod
    def _require_inputs(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("DerivedFact requires at least one input fact ID")
        return value
