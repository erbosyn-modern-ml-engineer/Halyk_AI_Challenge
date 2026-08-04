"""Document parsing contracts."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from halyk_agent.domain.common import JsonObject, NonEmptyStr
from halyk_agent.domain.evidence import EvidenceSpan


class ParsedTable(BaseModel):
    """Minimal table representation produced by a parser."""

    model_config = ConfigDict(extra="forbid")

    table_id: NonEmptyStr
    page_number: int = Field(ge=1)
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)


class ParsedDocument(BaseModel):
    """Parser output carrying text, tables, and provenance spans."""

    model_config = ConfigDict(extra="forbid")

    document_id: NonEmptyStr
    source_file: NonEmptyStr
    page_count: int = Field(ge=0)
    text: NonEmptyStr
    tables: list[ParsedTable] = Field(default_factory=list)
    spans: list[EvidenceSpan] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)


class ParseQualityReport(BaseModel):
    """Quality metrics used to accept or reject a parse result."""

    model_config = ConfigDict(extra="forbid")

    accepted: bool
    score: float = Field(ge=0.0, le=1.0)
    reasons: list[NonEmptyStr] = Field(default_factory=list)


@runtime_checkable
class DocumentParser(Protocol):
    """Parses raw document bytes into a structured representation."""

    async def parse(
        self,
        data: bytes,
        *,
        source_file: str,
        document_id: str,
        media_type: str | None = None,
    ) -> ParsedDocument:
        """Parse document bytes into text, tables, and spans."""
        ...


@runtime_checkable
class ParseQualityGate(Protocol):
    """Accepts or rejects parse output based on quality thresholds."""

    def evaluate(self, document: ParsedDocument) -> ParseQualityReport:
        """Return a quality report for the parsed document."""
        ...
