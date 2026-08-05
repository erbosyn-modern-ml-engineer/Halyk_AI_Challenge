"""Structure-aware retrieval chunk domain models."""

from __future__ import annotations

import math
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from halyk_agent.domain.common import JsonObject, JsonValue, NonEmptyStr
from halyk_agent.domain.ids import deterministic_id, sha256_text

CHUNK_SCHEMA_VERSION = "halyk.retrieval_chunk.v1"
CHUNK_MANIFEST_SCHEMA_VERSION = "halyk.chunk_manifest.v1"


class ChunkKind(StrEnum):
    """Structural chunk kinds justified by source document structure."""

    TEXT = "TEXT"
    PAGE = "PAGE"
    SECTION = "SECTION"
    TABLE = "TABLE"
    TABLE_ROW = "TABLE_ROW"
    TABLE_CELL = "TABLE_CELL"


class RetrievalTextKind(StrEnum):
    """How ``retrieval_text`` relates to exact source evidence."""

    RAW_SOURCE = "RAW_SOURCE"
    CONTEXT_ENRICHED = "CONTEXT_ENRICHED"
    SYNTHETIC_TABLE_SERIALIZATION = "SYNTHETIC_TABLE_SERIALIZATION"


class ChunkLevel(StrEnum):
    """Parent/child/atomic hierarchy level for retrieval chunks."""

    PARENT = "PARENT"
    CHILD = "CHILD"
    ATOMIC = "ATOMIC"


class ChunkerIdentity(BaseModel):
    """Identity of the chunker configuration that produced chunks."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: NonEmptyStr
    version: NonEmptyStr
    configuration_hash: NonEmptyStr
    normalization_version: NonEmptyStr


def _sorted_unique_strs(values: list[str]) -> list[str]:
    return sorted(set(values))


def _json_values_finite(value: JsonValue, *, path: str = "metadata") -> None:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must not contain NaN or Infinity")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _json_values_finite(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _json_values_finite(item, path=f"{path}.{key}")


class RetrievalChunk(BaseModel):
    """A deterministic retrieval unit with exact evidence lineage."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: NonEmptyStr
    document_id: NonEmptyStr
    document_version_id: NonEmptyStr
    artifact_id: NonEmptyStr
    source_file: NonEmptyStr
    kind: ChunkKind
    level: ChunkLevel
    page_numbers: list[int] = Field(min_length=1)
    raw_text: NonEmptyStr
    retrieval_text: NonEmptyStr
    retrieval_text_kind: RetrievalTextKind
    parent_chunk_id: NonEmptyStr | None = None
    source_block_ids: list[NonEmptyStr] = Field(default_factory=list)
    source_table_ids: list[NonEmptyStr] = Field(default_factory=list)
    evidence_span_ids: list[NonEmptyStr] = Field(min_length=1)
    heading_path: list[NonEmptyStr] = Field(default_factory=list)
    ordinal: int = Field(ge=0)
    character_count: int = Field(ge=1)
    estimated_token_count: int = Field(ge=0)
    metadata: JsonObject = Field(default_factory=dict)

    @field_validator("page_numbers")
    @classmethod
    def _page_numbers_sorted_unique(cls, value: list[int]) -> list[int]:
        if any(page < 1 for page in value):
            raise ValueError("page numbers must be 1-based")
        return sorted(set(value))

    @field_validator("source_block_ids", "source_table_ids", "evidence_span_ids")
    @classmethod
    def _sorted_id_lists(cls, value: list[str]) -> list[str]:
        return _sorted_unique_strs(value)

    @field_validator("raw_text", "retrieval_text")
    @classmethod
    def _non_empty_after_strip(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must be non-empty after stripping")
        return value

    @field_validator("metadata")
    @classmethod
    def _metadata_finite(cls, value: JsonObject) -> JsonObject:
        _json_values_finite(value)
        return value

    @model_validator(mode="after")
    def _invariants(self) -> RetrievalChunk:
        if self.character_count != len(self.raw_text):
            raise ValueError("character_count must equal len(raw_text)")
        if not self.evidence_span_ids:
            raise ValueError("chunk must contain at least one evidence span")
        if self.level is ChunkLevel.CHILD and self.parent_chunk_id is None:
            raise ValueError("child chunks must reference a parent_chunk_id")
        if (
            self.retrieval_text_kind is RetrievalTextKind.RAW_SOURCE
            and self.retrieval_text != self.raw_text
        ):
            raise ValueError("RAW_SOURCE retrieval_text must equal raw_text")
        # CONTEXT_ENRICHED / SYNTHETIC_TABLE_SERIALIZATION improve search only;
        # cite evidence_span_ids (never treat synthetic retrieval_text as evidence).
        return self


class ChunkManifest(BaseModel):
    """Canonical manifest for a deterministic chunking run (no timestamps)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: NonEmptyStr = CHUNK_MANIFEST_SCHEMA_VERSION
    chunker_identity: ChunkerIdentity
    source_parse_report_hash: NonEmptyStr
    documents: list[NonEmptyStr] = Field(default_factory=list)
    total_chunks: int = Field(ge=0)
    parent_chunks: int = Field(ge=0)
    child_chunks: int = Field(ge=0)
    table_chunks: int = Field(ge=0)
    chunks_sha256: NonEmptyStr

    @field_validator("documents")
    @classmethod
    def _sorted_documents(cls, value: list[str]) -> list[str]:
        return _sorted_unique_strs(value)

    @model_validator(mode="after")
    def _count_invariants(self) -> ChunkManifest:
        if self.parent_chunks + self.child_chunks > self.total_chunks:
            raise ValueError("parent_chunks + child_chunks cannot exceed total_chunks")
        if self.table_chunks > self.total_chunks:
            raise ValueError("table_chunks cannot exceed total_chunks")
        return self


def chunk_identity(
    *,
    document_id: str,
    chunker_configuration_hash: str,
    level: ChunkLevel,
    kind: ChunkKind,
    page_numbers: list[int],
    raw_text: str,
    retrieval_text: str,
    source_block_ids: list[str],
    source_table_ids: list[str],
    evidence_span_ids: list[str],
    ordinal: int,
) -> str:
    """Deterministic chunk ID from schema version and content lineage parts."""
    pages = ",".join(str(page) for page in sorted(set(page_numbers)))
    return deterministic_id(
        CHUNK_SCHEMA_VERSION,
        document_id,
        chunker_configuration_hash,
        level.value,
        kind.value,
        pages,
        sha256_text(raw_text),
        sha256_text(retrieval_text),
        ",".join(_sorted_unique_strs(list(source_block_ids))),
        ",".join(_sorted_unique_strs(list(source_table_ids))),
        ",".join(_sorted_unique_strs(list(evidence_span_ids))),
        ordinal,
    )


def chunks_content_sha256(chunks: list[RetrievalChunk]) -> str:
    """Stable hash over ordered chunk IDs and raw/retrieval text digests."""
    parts: list[str | bytes | int | float | None] = ["halyk.chunks-content.v1"]
    for chunk in sorted(chunks, key=lambda item: (item.ordinal, item.id)):
        parts.extend(
            [
                chunk.id,
                sha256_text(chunk.raw_text),
                sha256_text(chunk.retrieval_text),
            ]
        )
    return deterministic_id(*parts)
