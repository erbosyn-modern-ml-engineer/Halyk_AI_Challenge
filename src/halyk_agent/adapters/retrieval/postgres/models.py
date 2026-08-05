"""SQLAlchemy ORM models for FULL PostgreSQL hybrid retrieval."""

from __future__ import annotations

from typing import Any

from halyk_agent.adapters.retrieval.postgres.deps import ensure_postgres_available

ensure_postgres_available()

from pgvector.sqlalchemy import Vector  # noqa: E402
from sqlalchemy import (  # noqa: E402
    Boolean,
    ForeignKey,
    Integer,
    String,
    Text,
    false,
)
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship  # noqa: E402

DEFAULT_INDEX_KEY = "default"
FTS_CONFIG = "simple"


class Base(DeclarativeBase):
    """Declarative base for retrieval tables."""


class RetrievalChunkRow(Base):
    """Indexed retrieval chunk with filterable metadata and FTS source text."""

    __tablename__ = "retrieval_chunks"

    chunk_id: Mapped[str] = mapped_column(Text, primary_key=True)
    document_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    document_version_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    artifact_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    source_file: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    level: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    page_numbers: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    retrieval_text: Mapped[str] = mapped_column(Text, nullable=False)
    retrieval_text_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_chunk_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_block_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    source_table_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    evidence_span_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    heading_path: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    character_count: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    embedding: Mapped[RetrievalEmbeddingRow | None] = relationship(
        "RetrievalEmbeddingRow",
        back_populates="chunk",
        uselist=False,
        cascade="all, delete-orphan",
    )


class RetrievalEmbeddingRow(Base):
    """Dense embedding for a chunk (exact cosine search; no HNSW by default)."""

    __tablename__ = "retrieval_embeddings"

    chunk_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("retrieval_chunks.chunk_id", ondelete="CASCADE"),
        primary_key=True,
    )
    model_id: Mapped[str] = mapped_column(Text, nullable=False)
    model_revision: Mapped[str] = mapped_column(Text, nullable=False)
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(), nullable=False)
    vector_checksum: Mapped[str] = mapped_column(Text, nullable=False)

    chunk: Mapped[RetrievalChunkRow] = relationship(back_populates="embedding")


class RetrievalIndexRow(Base):
    """Index identity / readiness metadata (failed builds must not set ready)."""

    __tablename__ = "retrieval_indexes"

    index_key: Mapped[str] = mapped_column(Text, primary_key=True)
    index_identity: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    embedding_model: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    ready: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    vector_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


__all__ = [
    "DEFAULT_INDEX_KEY",
    "FTS_CONFIG",
    "Base",
    "RetrievalChunkRow",
    "RetrievalEmbeddingRow",
    "RetrievalIndexRow",
]
