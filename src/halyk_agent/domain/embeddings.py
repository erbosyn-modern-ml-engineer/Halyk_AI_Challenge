"""Embedding model identity and vector metadata (no full vectors)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from halyk_agent.domain.common import NonEmptyStr


class EmbeddingModelIdentity(BaseModel):
    """Pinned embedding or reranker model identity for reproducible indexes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    logical_name: NonEmptyStr
    model_id: NonEmptyStr
    revision: NonEmptyStr
    dimension: int | None = Field(default=None, ge=1)
    max_input_tokens: int | None = Field(default=None, ge=1)
    normalized: bool = False
    query_prefix: str = ""
    passage_prefix: str = ""
    license: NonEmptyStr


class EmbeddingRecord(BaseModel):
    """Canonical embedding metadata without serializing full vectors."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: NonEmptyStr
    model: EmbeddingModelIdentity
    vector_dimension: int = Field(ge=1)
    vector_checksum: NonEmptyStr

    @model_validator(mode="after")
    def _dimension_matches_model(self) -> EmbeddingRecord:
        if self.model.dimension is not None and self.vector_dimension != self.model.dimension:
            raise ValueError("vector_dimension must match model.dimension")
        return self
