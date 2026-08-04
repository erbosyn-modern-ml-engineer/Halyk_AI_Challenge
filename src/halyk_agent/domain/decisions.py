"""Internal decision result models."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from halyk_agent.domain.common import NonEmptyStr


class DecisionStatus(StrEnum):
    """Internal decision statuses (not the official competition submission enum)."""

    APPROVE = "APPROVE"
    REJECT = "REJECT"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class DecisionResult(BaseModel):
    """Internal decision outcome produced by the shared decision engine."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: DecisionStatus
    reason_codes: list[NonEmptyStr] = Field(default_factory=list)
    summary: NonEmptyStr
