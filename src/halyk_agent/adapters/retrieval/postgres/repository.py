"""Async PostgreSQL persistence for FULL hybrid retrieval."""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

from halyk_agent.adapters.retrieval.errors import IndexNotReadyError
from halyk_agent.adapters.retrieval.local.vectors import pack_float32_vector
from halyk_agent.adapters.retrieval.postgres.backend import (
    VectorBackendName,
    resolve_vector_backend,
)
from halyk_agent.adapters.retrieval.postgres.deps import (
    ensure_pgvector_package,
    ensure_postgres_available,
)
from halyk_agent.adapters.retrieval.postgres.filters import build_filter_sql
from halyk_agent.domain.chunking import RetrievalChunk
from halyk_agent.domain.embeddings import EmbeddingModelIdentity
from halyk_agent.domain.retrieval import IndexIdentity

ensure_postgres_available()

from sqlalchemy import delete, event, select, text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from halyk_agent.adapters.retrieval.postgres.models import (  # noqa: E402
    DEFAULT_INDEX_KEY,
    Base,
    RetrievalChunkRow,
    RetrievalEmbeddingRow,
    RetrievalIndexRow,
)


def _forced_backend_from_env() -> VectorBackendName | None:
    raw = os.environ.get("HALYK_VECTOR_BACKEND", "").strip().lower()
    if not raw:
        return None
    return VectorBackendName(raw)


def create_retrieval_engine(
    dsn: str, *, vector_backend: VectorBackendName | None = None
) -> AsyncEngine:
    """Create an async engine; register pgvector codec only for pgvector backend."""
    ensure_postgres_available()
    engine = create_async_engine(dsn, pool_pre_ping=True)
    if vector_backend is VectorBackendName.PGVECTOR:
        ensure_pgvector_package()
        from pgvector.asyncpg import register_vector

        @event.listens_for(engine.sync_engine, "connect")
        def _register_vector(dbapi_connection: Any, _connection_record: Any) -> None:
            dbapi_connection.run_async(register_vector)

    return engine


class PostgresRetrievalRepository:
    """Transactional index store (chunks, FTS source, embeddings, readiness)."""

    def __init__(
        self,
        dsn: str,
        *,
        index_key: str = DEFAULT_INDEX_KEY,
        engine: AsyncEngine | None = None,
        vector_backend: VectorBackendName | str | None = None,
    ) -> None:
        ensure_postgres_available()
        self.dsn = dsn
        self.index_key = index_key
        self._requested_backend: VectorBackendName | None
        if vector_backend is None:
            self._requested_backend = _forced_backend_from_env()
        else:
            self._requested_backend = VectorBackendName(str(vector_backend))
        self._backend: VectorBackendName | None = None
        # Engine may be created before backend detection; recreate if pgvector needed.
        self._engine = engine or create_async_engine(dsn, pool_pre_ping=True)
        self._owns_engine = engine is None
        self._session_factory = async_sessionmaker(
            self._engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @property
    def vector_backend(self) -> VectorBackendName | None:
        return self._backend

    def session(self) -> AsyncSession:
        return self._session_factory()

    async def dispose(self) -> None:
        if self._owns_engine:
            await self._engine.dispose()

    async def ensure_schema(self) -> None:
        """Create tables for the selected backend. Never installs pgvector."""
        backend = await resolve_vector_backend(
            self._engine,
            prefer_pgvector=True,
            forced=self._requested_backend,
        )
        self._backend = backend
        if backend is VectorBackendName.PGVECTOR:
            await self._ensure_pgvector_schema()
        else:
            await self._ensure_numpy_schema()

    async def _ensure_numpy_schema(self) -> None:
        async with self._engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await connection.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS ix_retrieval_chunks_fts
                    ON retrieval_chunks
                    USING GIN (to_tsvector('simple', retrieval_text))
                    """
                )
            )

    async def _ensure_pgvector_schema(self) -> None:
        ensure_pgvector_package()
        from pgvector.asyncpg import register_vector

        from halyk_agent.adapters.retrieval.postgres.models_pgvector import PgvectorBase

        # Rebuild engine with vector codec when we own it.
        if self._owns_engine:
            await self._engine.dispose()
            self._engine = create_retrieval_engine(
                self.dsn, vector_backend=VectorBackendName.PGVECTOR
            )
            self._session_factory = async_sessionmaker(
                self._engine,
                expire_on_commit=False,
                class_=AsyncSession,
            )
        else:

            @event.listens_for(self._engine.sync_engine, "connect")
            def _register_vector(dbapi_connection: Any, _connection_record: Any) -> None:
                dbapi_connection.run_async(register_vector)

        async with self._engine.begin() as connection:
            # Extension must already exist — never CREATE EXTENSION here.
            await connection.run_sync(PgvectorBase.metadata.create_all)
            await connection.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS ix_retrieval_chunks_fts
                    ON retrieval_chunks
                    USING GIN (to_tsvector('simple', retrieval_text))
                    """
                )
            )

    async def replace_index(
        self,
        *,
        chunks: Sequence[RetrievalChunk],
        embeddings: dict[str, list[float]],
        model_identity: EmbeddingModelIdentity,
        index_identity: IndexIdentity,
    ) -> None:
        """Atomically rebuild index contents; ready is set only on success."""
        if self._backend is None:
            await self.ensure_schema()
        assert self._backend is not None
        if self._backend is VectorBackendName.PGVECTOR:
            await self._replace_index_pgvector(
                chunks=chunks,
                embeddings=embeddings,
                model_identity=model_identity,
                index_identity=index_identity,
            )
        else:
            await self._replace_index_numpy(
                chunks=chunks,
                embeddings=embeddings,
                model_identity=model_identity,
                index_identity=index_identity,
            )

    async def _replace_index_numpy(
        self,
        *,
        chunks: Sequence[RetrievalChunk],
        embeddings: dict[str, list[float]],
        model_identity: EmbeddingModelIdentity,
        index_identity: IndexIdentity,
    ) -> None:
        unknown = set(embeddings) - {chunk.id for chunk in chunks}
        if unknown:
            raise ValueError(f"embeddings reference unknown chunk ids: {sorted(unknown)[:5]}")
        expected_dim = model_identity.dimension
        identity_payload = index_identity.model_dump(mode="json")
        identity_payload.setdefault("lexical_configuration", {})
        identity_payload["lexical_configuration"]["vector_backend"] = str(
            VectorBackendName.POSTGRES_NUMPY_EXACT
        )
        async with self._session_factory() as session, session.begin():
            await session.execute(delete(RetrievalEmbeddingRow))
            await session.execute(delete(RetrievalChunkRow))
            await session.execute(
                delete(RetrievalIndexRow).where(RetrievalIndexRow.index_key == self.index_key)
            )
            for chunk in chunks:
                session.add(_chunk_to_row(chunk))
                vector = embeddings.get(chunk.id)
                if vector is None:
                    continue
                blob, dimension, checksum = pack_float32_vector(vector)
                if expected_dim is not None and dimension != expected_dim:
                    raise ValueError(
                        f"embedding dimension {dimension} does not match "
                        f"model dimension {expected_dim} for chunk {chunk.id}"
                    )
                session.add(
                    RetrievalEmbeddingRow(
                        chunk_id=chunk.id,
                        model_id=model_identity.model_id,
                        model_revision=model_identity.revision,
                        dimension=dimension,
                        embedding_blob=blob,
                        vector_checksum=checksum,
                    )
                )
            session.add(
                RetrievalIndexRow(
                    index_key=self.index_key,
                    index_identity=identity_payload,
                    embedding_model=model_identity.model_dump(mode="json"),
                    ready=True,
                    chunk_count=len(chunks),
                    vector_count=len(embeddings),
                    vector_backend=str(VectorBackendName.POSTGRES_NUMPY_EXACT),
                )
            )

    async def _replace_index_pgvector(
        self,
        *,
        chunks: Sequence[RetrievalChunk],
        embeddings: dict[str, list[float]],
        model_identity: EmbeddingModelIdentity,
        index_identity: IndexIdentity,
    ) -> None:
        from halyk_agent.adapters.retrieval.postgres.models_pgvector import (
            PgvectorChunkRow,
            PgvectorEmbeddingRow,
            PgvectorIndexRow,
        )

        unknown = set(embeddings) - {chunk.id for chunk in chunks}
        if unknown:
            raise ValueError(f"embeddings reference unknown chunk ids: {sorted(unknown)[:5]}")
        expected_dim = model_identity.dimension
        identity_payload = index_identity.model_dump(mode="json")
        identity_payload.setdefault("lexical_configuration", {})
        identity_payload["lexical_configuration"]["vector_backend"] = str(
            VectorBackendName.PGVECTOR
        )
        async with self._session_factory() as session, session.begin():
            await session.execute(delete(PgvectorEmbeddingRow))
            await session.execute(delete(PgvectorChunkRow))
            await session.execute(
                delete(PgvectorIndexRow).where(PgvectorIndexRow.index_key == self.index_key)
            )
            for chunk in chunks:
                session.add(_chunk_to_pgvector_row(chunk))
                vector = embeddings.get(chunk.id)
                if vector is None:
                    continue
                _blob, dimension, checksum = pack_float32_vector(vector)
                if expected_dim is not None and dimension != expected_dim:
                    raise ValueError(
                        f"embedding dimension {dimension} does not match "
                        f"model dimension {expected_dim} for chunk {chunk.id}"
                    )
                session.add(
                    PgvectorEmbeddingRow(
                        chunk_id=chunk.id,
                        model_id=model_identity.model_id,
                        model_revision=model_identity.revision,
                        dimension=dimension,
                        embedding=list(vector),
                        vector_checksum=checksum,
                    )
                )
            session.add(
                PgvectorIndexRow(
                    index_key=self.index_key,
                    index_identity=identity_payload,
                    embedding_model=model_identity.model_dump(mode="json"),
                    ready=True,
                    chunk_count=len(chunks),
                    vector_count=len(embeddings),
                    vector_backend=str(VectorBackendName.PGVECTOR),
                )
            )

    async def require_ready(self) -> None:
        await self._require_index_row()

    async def load_index_identity(self) -> IndexIdentity:
        row = await self._require_index_row()
        return IndexIdentity.model_validate(row["index_identity"])

    async def load_embedding_model(self) -> EmbeddingModelIdentity:
        row = await self._require_index_row()
        return EmbeddingModelIdentity.model_validate(row["embedding_model"])

    async def load_vector_backend(self) -> VectorBackendName:
        row = await self._require_index_row()
        backend = row.get("vector_backend")
        if backend:
            self._backend = VectorBackendName(str(backend))
            return self._backend
        # Legacy rows without column: inspect identity payload.
        identity = row["index_identity"]
        if isinstance(identity, dict):
            lex = identity.get("lexical_configuration") or {}
            if isinstance(lex, dict) and lex.get("vector_backend"):
                self._backend = VectorBackendName(str(lex["vector_backend"]))
                return self._backend
        self._backend = VectorBackendName.POSTGRES_NUMPY_EXACT
        return self._backend

    async def vector_count(self) -> int:
        async with self._session_factory() as session:
            # Table name is shared; count works for both backends.
            result = await session.execute(text("SELECT COUNT(*) FROM retrieval_embeddings"))
            return int(result.scalar_one())

    async def get_chunks(self, chunk_ids: Sequence[str]) -> dict[str, RetrievalChunk]:
        if not chunk_ids:
            return {}
        async with self._session_factory() as session:
            result = await session.execute(
                select(RetrievalChunkRow).where(RetrievalChunkRow.chunk_id.in_(list(chunk_ids)))
            )
            rows = result.scalars().all()
            return {row.chunk_id: _row_to_chunk(row) for row in rows}

    async def _require_index_row(self) -> dict[str, Any]:
        async with self._session_factory() as session:
            result = await session.execute(
                text(
                    """
                    SELECT index_key, index_identity, embedding_model, ready,
                           chunk_count, vector_count,
                           COALESCE(vector_backend, 'postgres_numpy_exact') AS vector_backend
                    FROM retrieval_indexes
                    WHERE index_key = :index_key
                    """
                ),
                {"index_key": self.index_key},
            )
            row = result.mappings().first()
            if row is None or row["ready"] is not True:
                raise IndexNotReadyError("PostgreSQL retrieval index is not ready")
            return dict(row)


def _chunk_to_row(chunk: RetrievalChunk) -> RetrievalChunkRow:
    return RetrievalChunkRow(
        chunk_id=chunk.id,
        document_id=chunk.document_id,
        document_version_id=chunk.document_version_id,
        artifact_id=chunk.artifact_id,
        source_file=chunk.source_file,
        kind=chunk.kind.value,
        level=chunk.level.value,
        page_numbers=list(chunk.page_numbers),
        raw_text=chunk.raw_text,
        retrieval_text=chunk.retrieval_text,
        retrieval_text_kind=chunk.retrieval_text_kind.value,
        parent_chunk_id=chunk.parent_chunk_id,
        source_block_ids=list(chunk.source_block_ids),
        source_table_ids=list(chunk.source_table_ids),
        evidence_span_ids=list(chunk.evidence_span_ids),
        heading_path=list(chunk.heading_path),
        ordinal=chunk.ordinal,
        character_count=chunk.character_count,
        estimated_token_count=chunk.estimated_token_count,
        metadata_json=dict(chunk.metadata),
    )


def _chunk_to_pgvector_row(chunk: RetrievalChunk) -> Any:
    from halyk_agent.adapters.retrieval.postgres.models_pgvector import PgvectorChunkRow

    return PgvectorChunkRow(
        chunk_id=chunk.id,
        document_id=chunk.document_id,
        document_version_id=chunk.document_version_id,
        artifact_id=chunk.artifact_id,
        source_file=chunk.source_file,
        kind=chunk.kind.value,
        level=chunk.level.value,
        page_numbers=list(chunk.page_numbers),
        raw_text=chunk.raw_text,
        retrieval_text=chunk.retrieval_text,
        retrieval_text_kind=chunk.retrieval_text_kind.value,
        parent_chunk_id=chunk.parent_chunk_id,
        source_block_ids=list(chunk.source_block_ids),
        source_table_ids=list(chunk.source_table_ids),
        evidence_span_ids=list(chunk.evidence_span_ids),
        heading_path=list(chunk.heading_path),
        ordinal=chunk.ordinal,
        character_count=chunk.character_count,
        estimated_token_count=chunk.estimated_token_count,
        metadata_json=dict(chunk.metadata),
    )


def _row_to_chunk(row: RetrievalChunkRow) -> RetrievalChunk:
    return RetrievalChunk.model_validate(
        {
            "id": row.chunk_id,
            "document_id": row.document_id,
            "document_version_id": row.document_version_id,
            "artifact_id": row.artifact_id,
            "source_file": row.source_file,
            "kind": row.kind,
            "level": row.level,
            "page_numbers": row.page_numbers,
            "raw_text": row.raw_text,
            "retrieval_text": row.retrieval_text,
            "retrieval_text_kind": row.retrieval_text_kind,
            "parent_chunk_id": row.parent_chunk_id,
            "source_block_ids": row.source_block_ids,
            "source_table_ids": row.source_table_ids,
            "evidence_span_ids": row.evidence_span_ids,
            "heading_path": row.heading_path,
            "ordinal": row.ordinal,
            "character_count": row.character_count,
            "estimated_token_count": row.estimated_token_count,
            "metadata": row.metadata_json,
        }
    )


__all__ = [
    "PostgresRetrievalRepository",
    "build_filter_sql",
    "create_retrieval_engine",
]
