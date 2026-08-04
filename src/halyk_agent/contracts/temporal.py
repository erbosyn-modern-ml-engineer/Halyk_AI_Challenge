"""Temporal fact storage and document version resolution contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from halyk_agent.domain.common import JsonValue, NonEmptyStr
from halyk_agent.domain.documents import ApplicableVersionSet, DocumentVersionRef
from halyk_agent.domain.evidence import EvidenceSpan


class TemporalFact(BaseModel):
    """Bi-temporal fact record with provenance."""

    model_config = ConfigDict(extra="forbid")

    fact_id: NonEmptyStr
    fact_type: NonEmptyStr
    subject_id: NonEmptyStr
    value: JsonValue
    valid_at: datetime | None = None
    invalid_at: datetime | None = None
    expired_at: datetime | None = None
    reference_time: datetime | None = None
    evidence: list[EvidenceSpan] = Field(default_factory=list)
    source_fact_ids: list[NonEmptyStr] = Field(default_factory=list)


@runtime_checkable
class TemporalFactStore(Protocol):
    """Stores and queries temporally versioned facts."""

    async def upsert(self, fact: TemporalFact) -> NonEmptyStr:
        """Insert or update a temporal fact and return its ID."""
        ...

    async def as_of(
        self,
        *,
        subject_id: str,
        at: datetime,
        fact_type: str | None = None,
    ) -> list[TemporalFact]:
        """Return facts valid at the given reference time."""
        ...


@runtime_checkable
class DocumentVersionResolver(Protocol):
    """Deterministically selects applicable document versions (no LLM)."""

    def resolve(
        self,
        versions: list[DocumentVersionRef],
        *,
        as_of: datetime,
    ) -> ApplicableVersionSet:
        """Resolve applicable versions and report conflicts."""
        ...


# Re-export for type checkers that resolve facts through temporal adapters.
__all__ = [
    "DocumentVersionResolver",
    "TemporalFact",
    "TemporalFactStore",
]
