"""Rule reference domain models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from halyk_agent.domain.common import NonEmptyStr


class RuleRef(BaseModel):
    """Reference to an applicable decision rule version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: NonEmptyStr
    rule_version: NonEmptyStr
