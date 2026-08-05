"""Retrieval query, hit, result, and index identity domain models."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from halyk_agent.domain.chunking import (
    ChunkerIdentity,
    ChunkKind,
    ChunkLevel,
    RetrievalChunk,
)
from halyk_agent.domain.common import JsonObject, NonEmptyStr
from halyk_agent.domain.embeddings import EmbeddingModelIdentity

INDEX_IDENTITY_SCHEMA_VERSION = "halyk.index_identity.v1"
INDEX_REPORT_SCHEMA_VERSION = "halyk.index_report.v1"

# Bounded configurable maxima for retrieval queries (Stage 4 defaults).
MAX_TOP_K = 100
MAX_CANDIDATE_K = 1000


class MatchedBy(StrEnum):
    """How a hit entered the final ranked set."""

    LEXICAL = "LEXICAL"
    VECTOR = "VECTOR"
    HYBRID = "HYBRID"
    RERANKED = "RERANKED"


class RetrievalFilters(BaseModel):
    """Hard filters applied before ranking (no as_of applicability)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_ids: list[NonEmptyStr] = Field(default_factory=list)
    document_version_ids: list[NonEmptyStr] = Field(default_factory=list)
    artifact_ids: list[NonEmptyStr] = Field(default_factory=list)
    source_files: list[NonEmptyStr] = Field(default_factory=list)
    page_numbers: list[int] = Field(default_factory=list)
    chunk_kinds: list[ChunkKind] = Field(default_factory=list)
    chunk_levels: list[ChunkLevel] = Field(default_factory=list)

    @field_validator(
        "document_ids",
        "document_version_ids",
        "artifact_ids",
        "source_files",
    )
    @classmethod
    def _sorted_unique_strs(cls, value: list[str]) -> list[str]:
        return sorted(set(value))

    @field_validator("page_numbers")
    @classmethod
    def _sorted_unique_pages(cls, value: list[int]) -> list[int]:
        if any(page < 1 for page in value):
            raise ValueError("page numbers must be 1-based")
        return sorted(set(value))

    @field_validator("chunk_kinds")
    @classmethod
    def _sorted_kinds(cls, value: list[ChunkKind]) -> list[ChunkKind]:
        return sorted(set(value), key=lambda item: item.value)

    @field_validator("chunk_levels")
    @classmethod
    def _sorted_levels(cls, value: list[ChunkLevel]) -> list[ChunkLevel]:
        return sorted(set(value), key=lambda item: item.value)


class RetrievalQuery(BaseModel):
    """Bounded retrieval request with hard filters."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: NonEmptyStr
    filters: RetrievalFilters = Field(default_factory=RetrievalFilters)
    top_k: int = Field(default=10, ge=1, le=MAX_TOP_K)
    lexical_candidate_k: int = Field(default=50, ge=1, le=MAX_CANDIDATE_K)
    vector_candidate_k: int = Field(default=50, ge=1, le=MAX_CANDIDATE_K)
    rerank_candidate_k: int = Field(default=50, ge=1, le=MAX_CANDIDATE_K)
    include_parent_context: bool = False

    @field_validator("text")
    @classmethod
    def _non_empty_query(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("query text must be non-empty")
        return stripped

    @model_validator(mode="after")
    def _candidate_bounds(self) -> RetrievalQuery:
        if self.lexical_candidate_k < self.top_k:
            raise ValueError("lexical_candidate_k must be >= top_k")
        if self.vector_candidate_k < self.top_k:
            raise ValueError("vector_candidate_k must be >= top_k")
        if self.rerank_candidate_k < self.top_k:
            raise ValueError("rerank_candidate_k must be >= top_k")
        return self


class IndexIdentity(BaseModel):
    """Canonical index identity (no timestamps)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: NonEmptyStr = INDEX_IDENTITY_SCHEMA_VERSION
    profile: NonEmptyStr
    chunk_manifest_hash: NonEmptyStr
    chunker_identity: ChunkerIdentity
    embedding_model: EmbeddingModelIdentity
    lexical_configuration: JsonObject = Field(default_factory=dict)
    rrf_configuration: JsonObject = Field(default_factory=dict)
    reranker_model: EmbeddingModelIdentity | None = None


class RetrievalHit(BaseModel):
    """Ranked retrieval hit retaining component ranks and scores."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk: RetrievalChunk
    lexical_rank: int | None = Field(default=None, ge=1)
    lexical_score: float | None = None
    vector_rank: int | None = Field(default=None, ge=1)
    vector_score: float | None = None
    rrf_score: float | None = None
    rerank_score: float | None = None
    final_rank: int = Field(ge=1)
    matched_by: MatchedBy
    expanded_parent: RetrievalChunk | None = None

    @model_validator(mode="after")
    def _parent_same_document(self) -> RetrievalHit:
        parent = self.expanded_parent
        if parent is None:
            return self
        if parent.document_id != self.chunk.document_id:
            raise ValueError("expanded_parent must belong to the same document")
        if parent.document_version_id != self.chunk.document_version_id:
            raise ValueError("expanded_parent must belong to the same document version")
        return self


class RetrievalResult(BaseModel):
    """Retrieval response with index and model identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    query: RetrievalQuery
    hits: list[RetrievalHit] = Field(default_factory=list)
    index_identity: IndexIdentity
    embedding_model: EmbeddingModelIdentity
    reranker_model: EmbeddingModelIdentity | None = None
    warnings: list[NonEmptyStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def _hits_ordered(self) -> RetrievalResult:
        ranks = [hit.final_rank for hit in self.hits]
        if ranks != sorted(ranks):
            raise ValueError("hits must be ordered by final_rank ascending")
        if len(set(ranks)) != len(ranks):
            raise ValueError("final_rank values must be unique within a result")
        return self


class IndexReport(BaseModel):
    """Operational index build report (canonical identity excludes durations)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: NonEmptyStr = INDEX_REPORT_SCHEMA_VERSION
    profile: NonEmptyStr
    chunk_count: int = Field(ge=0)
    indexed_lexically: int = Field(ge=0)
    indexed_vectors: int = Field(ge=0)
    skipped_chunks: list[NonEmptyStr] = Field(default_factory=list)
    failures: list[NonEmptyStr] = Field(default_factory=list)
    embedding_model: EmbeddingModelIdentity
    index_identity: IndexIdentity

    @model_validator(mode="after")
    def _counts(self) -> IndexReport:
        if self.indexed_lexically > self.chunk_count:
            raise ValueError("indexed_lexically cannot exceed chunk_count")
        if self.indexed_vectors > self.chunk_count:
            raise ValueError("indexed_vectors cannot exceed chunk_count")
        if self.index_identity.profile != self.profile:
            raise ValueError("index_identity.profile must match report profile")
        return self
