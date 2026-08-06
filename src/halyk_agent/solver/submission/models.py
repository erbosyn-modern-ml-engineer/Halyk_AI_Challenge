"""Typed submission contract matching the competition template."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


class CovenantStatus(StrEnum):
    BREACH = "BREACH"
    COMPLIANT = "COMPLIANT"


class CovenantCell(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CovenantStatus | None = None
    actual: Decimal | float | int | None = None
    evidence_txn_id: str | None = None


class SubmissionDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team: str
    contact_email: str
    model: str
    answers: dict[str, dict[str, CovenantCell]]

    @field_validator("answers")
    @classmethod
    def _nonempty(
        cls, value: dict[str, dict[str, CovenantCell]]
    ) -> dict[str, dict[str, CovenantCell]]:
        if not value:
            raise ValueError("answers must be non-empty")
        return value


def load_template_dict(payload: dict[str, Any]) -> SubmissionDocument:
    return SubmissionDocument.model_validate(payload)
