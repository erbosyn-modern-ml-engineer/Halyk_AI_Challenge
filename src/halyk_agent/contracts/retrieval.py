"""Embedding and retrieval contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from halyk_agent.domain.common import JsonObject, NonEmptyStr
from halyk_agent.domain.evidence import EvidenceSpan


class EmbeddingVector(BaseModel):
    """Embedding payload with model identity and dimensionality."""

    model_config = ConfigDict(extra="forbid")

    model_id: NonEmptyStr
    dimensions: int = Field(ge=1)
    values: list[float] = Field(min_length=1)


class RetrievalFilter(BaseModel):
    """Hard filters applied before ranking."""

    model_config = ConfigDict(extra="forbid")

    document_ids: list[NonEmptyStr] = Field(default_factory=list)
    document_version_ids: list[NonEmptyStr] = Field(default_factory=list)
    as_of: datetime | None = None
    metadata: JsonObject = Field(default_factory=dict)


class EvidenceCandidate(BaseModel):
    """Ranked evidence candidate returned by retrieval."""

    model_config = ConfigDict(extra="forbid")

    span: EvidenceSpan
    score: float
    rank: int = Field(ge=1)
    lexical_rank: int | None = Field(default=None, ge=1)
    vector_rank: int | None = Field(default=None, ge=1)
    metadata: JsonObject = Field(default_factory=dict)


class EvidenceBundle(BaseModel):
    """Gathered evidence with budget/cost accounting."""

    model_config = ConfigDict(extra="forbid")

    query: NonEmptyStr
    candidates: list[EvidenceCandidate] = Field(default_factory=list)
    source_document_ids: list[NonEmptyStr] = Field(default_factory=list)
    cost_units: float = Field(default=0.0, ge=0.0)


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Creates embeddings for retrieval indexing and queries."""

    async def embed(self, texts: list[str]) -> list[EmbeddingVector]:
        """Embed texts with a consistent model and dimensionality."""
        ...


@runtime_checkable
class RetrievalEngine(Protocol):
    """Retrieves evidence candidates using profile-specific backends."""

    async def retrieve(
        self,
        query: str,
        *,
        filters: RetrievalFilter,
        limit: int = 20,
    ) -> list[EvidenceCandidate]:
        """Apply hard filters, then rank and return candidates."""
        ...


@runtime_checkable
class EvidenceGatherer(Protocol):
    """Collects a bounded, diverse evidence set for a task question."""

    async def gather(
        self,
        query: str,
        *,
        filters: RetrievalFilter,
        limit: int,
    ) -> EvidenceBundle:
        """Gather evidence under the configured depth budget."""
        ...
