"""Optional pgvector ORM models (only when extension already installed)."""

from __future__ import annotations

from typing import Any

from halyk_agent.adapters.retrieval.postgres.deps import ensure_pgvector_package

ensure_pgvector_package()

from pgvector.sqlalchemy import VECTOR  # noqa: E402
from sqlalchemy import (  # noqa: E402
    Boolean,
    Dialect,
    ForeignKey,
    Integer,
    String,
    Text,
    false,
)
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship  # noqa: E402

DEFAULT_INDEX_KEY = "default"


class AsyncpgCompatibleVector(VECTOR):
    """VECTOR bind that keeps lists for asyncpg codecs (avoids string round-trip).

    pgvector.sqlalchemy.VECTOR.bind_processor always stringifies via Vector._to_db.
    asyncpg+register_vector expects list/ndarray. Narrow dialect-specific override.
    """

    cache_ok = True

    def bind_processor(self, dialect: Dialect) -> Any:
        if dialect.name == "postgresql" and dialect.driver == "asyncpg":

            def process(value: Any) -> Any:
                if value is None:
                    return None
                if isinstance(value, list):
                    return [float(item) for item in value]
                return value

            return process
        return super().bind_processor(dialect)


Vector = AsyncpgCompatibleVector


class PgvectorBase(DeclarativeBase):
    """Declarative base for optional pgvector-backed tables."""


class PgvectorChunkRow(PgvectorBase):
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

    embeddings: Mapped[list[PgvectorEmbeddingRow]] = relationship(
        "PgvectorEmbeddingRow",
        back_populates="chunk",
        cascade="all, delete-orphan",
    )


class PgvectorEmbeddingRow(PgvectorBase):
    __tablename__ = "retrieval_embeddings"

    chunk_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("retrieval_chunks.chunk_id", ondelete="CASCADE"),
        primary_key=True,
    )
    model_id: Mapped[str] = mapped_column(Text, primary_key=True)
    model_revision: Mapped[str] = mapped_column(Text, primary_key=True)
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[Any] = mapped_column(Vector(), nullable=False)
    vector_checksum: Mapped[str] = mapped_column(Text, nullable=False)

    chunk: Mapped[PgvectorChunkRow] = relationship(back_populates="embeddings")


class PgvectorIndexRow(PgvectorBase):
    __tablename__ = "retrieval_indexes"

    index_key: Mapped[str] = mapped_column(Text, primary_key=True)
    index_identity: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    embedding_model: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    ready: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    vector_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    vector_backend: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="pgvector",
        server_default="pgvector",
    )


__all__ = [
    "DEFAULT_INDEX_KEY",
    "AsyncpgCompatibleVector",
    "PgvectorBase",
    "PgvectorChunkRow",
    "PgvectorEmbeddingRow",
    "PgvectorIndexRow",
    "Vector",
]
