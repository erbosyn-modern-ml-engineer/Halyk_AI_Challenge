"""Document parsing contracts — single authoritative parser Protocol."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from halyk_agent.domain.common import NonEmptyStr
from halyk_agent.domain.datasets import ArtifactFormat
from halyk_agent.domain.parsing import (
    CanonicalDocument,
    ParseResult,
    QualityDecision,
)


class ParseRequest(BaseModel):
    """Input shared by FAST and FULL document parsers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: NonEmptyStr
    source_path: Path
    format: ArtifactFormat
    source_file: NonEmptyStr
    source_sha256: NonEmptyStr
    document_id: NonEmptyStr | None = None
    document_version_id: NonEmptyStr | None = None
    mime_type: NonEmptyStr | None = None


class ParseQualityReport(BaseModel):
    """Quality metrics used to accept, fallback, or reject a parse result."""

    model_config = ConfigDict(extra="forbid")

    decision: QualityDecision
    accepted: bool
    score: float = Field(ge=0.0, le=1.0)
    triggered_rules: list[NonEmptyStr] = Field(default_factory=list)
    reasons: list[NonEmptyStr] = Field(default_factory=list)


@runtime_checkable
class DocumentParser(Protocol):
    """Parses a source file into a canonical ParseResult."""

    async def parse(self, request: ParseRequest) -> ParseResult:
        """Parse using the request's source path and identity fields."""
        ...


@runtime_checkable
class ParseQualityGate(Protocol):
    """Accepts or rejects parse output based on quality thresholds."""

    def evaluate(
        self,
        document: CanonicalDocument,
        *,
        profile: str,
    ) -> ParseQualityReport:
        """Return a quality report for the canonical document."""
        ...
