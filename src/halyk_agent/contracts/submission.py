"""Submission adapter contracts."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from halyk_agent.domain.common import JsonObject, NonEmptyStr
from halyk_agent.domain.proof import ProofBundle


class SubmissionArtifact(BaseModel):
    """Schema-validated submission payload ready for export."""

    model_config = ConfigDict(extra="forbid")

    case_id: NonEmptyStr
    schema_version: NonEmptyStr
    payload: JsonObject = Field(default_factory=dict)
    output_path: NonEmptyStr | None = None


@runtime_checkable
class SubmissionAdapter(Protocol):
    """Maps an internal proof bundle to the competition submission schema."""

    def render(self, proof: ProofBundle, *, template: JsonObject) -> SubmissionArtifact:
        """Render a schema-valid submission artifact from a proof bundle."""
        ...

    def validate(self, artifact: SubmissionArtifact) -> None:
        """Validate an artifact against the competition schema."""
        ...
