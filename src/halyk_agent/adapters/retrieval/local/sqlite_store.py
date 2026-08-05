"""SQLite persistence for FAST local hybrid retrieval."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from halyk_agent.adapters.retrieval.errors import (
    Fts5UnavailableError,
    IndexNotReadyError,
)
from halyk_agent.adapters.retrieval.local.schema import (
    INDEX_META_KEY_CHUNK_COUNT,
    INDEX_META_KEY_EMBEDDING_MODEL,
    INDEX_META_KEY_IDENTITY,
    INDEX_META_KEY_READY,
    INDEX_META_KEY_VECTOR_COUNT,
    SCHEMA_STATEMENTS,
)
from halyk_agent.adapters.retrieval.local.vectors import (
    pack_float32_vector,
    unpack_float32_vector,
)
from halyk_agent.domain.chunking import RetrievalChunk
from halyk_agent.domain.embeddings import EmbeddingModelIdentity
from halyk_agent.domain.retrieval import IndexIdentity, RetrievalFilters


def ensure_fts5_available(connection: sqlite3.Connection) -> None:
    """Raise ``Fts5UnavailableError`` when FTS5 is missing (no silent downgrade)."""
    try:
        used = connection.execute("SELECT sqlite_compileoption_used('ENABLE_FTS5')").fetchone()
        if used is not None and int(used[0]) == 1:
            return
    except sqlite3.Error:
        pass
    try:
        connection.execute("CREATE VIRTUAL TABLE temp._fts5_probe USING fts5(x)")
        connection.execute("DROP TABLE temp._fts5_probe")
    except sqlite3.Error as exc:
        raise Fts5UnavailableError(
            "SQLite FTS5 is unavailable; lexical index cannot be created"
        ) from exc


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class SqliteRetrievalStore:
    """Transactional local index store (chunks, FTS5, embeddings, metadata)."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._connection: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        if self._connection is not None:
            return self._connection
        if self.db_path.parent and str(self.db_path.parent) not in ("", "."):
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.db_path))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        self._connection = connection
        return connection

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def initialize_schema(self) -> None:
        connection = self.connect()
        ensure_fts5_available(connection)
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        connection.commit()

    def replace_index(
        self,
        *,
        chunks: Sequence[RetrievalChunk],
        embeddings: dict[str, list[float]],
        model_identity: EmbeddingModelIdentity,
        index_identity: IndexIdentity,
    ) -> None:
        """Atomically rebuild the index contents."""
        connection = self.connect()
        ensure_fts5_available(connection)
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in SCHEMA_STATEMENTS:
                connection.execute(statement)
            connection.execute("DELETE FROM chunks_fts")
            connection.execute("DELETE FROM embedding_records")
            connection.execute("DELETE FROM chunk_metadata")
            connection.execute("DELETE FROM chunks")
            connection.execute("DELETE FROM index_metadata")

            expected_dim = model_identity.dimension
            for chunk in chunks:
                self._insert_chunk(connection, chunk)
                vector = embeddings.get(chunk.id)
                if vector is None:
                    continue
                blob, dimension, checksum = pack_float32_vector(vector)
                if expected_dim is not None and dimension != expected_dim:
                    raise ValueError(
                        f"embedding dimension {dimension} does not match "
                        f"model dimension {expected_dim} for chunk {chunk.id}"
                    )
                connection.execute(
                    """
                    INSERT INTO embedding_records (
                        chunk_id, model_id, model_revision, dimension,
                        vector_blob, vector_checksum
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk.id,
                        model_identity.model_id,
                        model_identity.revision,
                        dimension,
                        blob,
                        checksum,
                    ),
                )

            self._set_meta(
                connection,
                INDEX_META_KEY_IDENTITY,
                index_identity.model_dump(mode="json"),
            )
            self._set_meta(
                connection,
                INDEX_META_KEY_EMBEDDING_MODEL,
                model_identity.model_dump(mode="json"),
            )
            self._set_meta(connection, INDEX_META_KEY_CHUNK_COUNT, len(chunks))
            self._set_meta(connection, INDEX_META_KEY_VECTOR_COUNT, len(embeddings))
            self._set_meta(connection, INDEX_META_KEY_READY, True)
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def _insert_chunk(self, connection: sqlite3.Connection, chunk: RetrievalChunk) -> None:
        page_json = _json_dumps(chunk.page_numbers)
        connection.execute(
            """
            INSERT INTO chunks (
                chunk_id, document_id, document_version_id, artifact_id, source_file,
                kind, level, page_numbers_json, raw_text, retrieval_text,
                retrieval_text_kind, parent_chunk_id, source_block_ids_json,
                source_table_ids_json, evidence_span_ids_json, heading_path_json,
                ordinal, character_count, estimated_token_count, metadata_json
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                chunk.id,
                chunk.document_id,
                chunk.document_version_id,
                chunk.artifact_id,
                chunk.source_file,
                chunk.kind.value,
                chunk.level.value,
                page_json,
                chunk.raw_text,
                chunk.retrieval_text,
                chunk.retrieval_text_kind.value,
                chunk.parent_chunk_id,
                _json_dumps(chunk.source_block_ids),
                _json_dumps(chunk.source_table_ids),
                _json_dumps(chunk.evidence_span_ids),
                _json_dumps(chunk.heading_path),
                chunk.ordinal,
                chunk.character_count,
                chunk.estimated_token_count,
                _json_dumps(chunk.metadata),
            ),
        )
        connection.execute(
            """
            INSERT INTO chunk_metadata (
                chunk_id, document_id, document_version_id, artifact_id,
                source_file, kind, level, page_numbers_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chunk.id,
                chunk.document_id,
                chunk.document_version_id,
                chunk.artifact_id,
                chunk.source_file,
                chunk.kind.value,
                chunk.level.value,
                page_json,
            ),
        )
        connection.execute(
            """
            INSERT INTO chunks_fts (chunk_id, retrieval_text)
            VALUES (?, ?)
            """,
            (chunk.id, chunk.retrieval_text),
        )

    @staticmethod
    def _set_meta(connection: sqlite3.Connection, key: str, value: Any) -> None:
        connection.execute(
            """
            INSERT INTO index_metadata (key, value_json) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json
            """,
            (key, _json_dumps(value)),
        )

    def require_ready(self) -> None:
        connection = self.connect()
        row = connection.execute(
            "SELECT value_json FROM index_metadata WHERE key = ?",
            (INDEX_META_KEY_READY,),
        ).fetchone()
        if row is None or json.loads(row["value_json"]) is not True:
            raise IndexNotReadyError()

    def load_index_identity(self) -> IndexIdentity:
        return IndexIdentity.model_validate(self._get_meta(INDEX_META_KEY_IDENTITY))

    def load_embedding_model(self) -> EmbeddingModelIdentity:
        return EmbeddingModelIdentity.model_validate(self._get_meta(INDEX_META_KEY_EMBEDDING_MODEL))

    def vector_count(self) -> int:
        connection = self.connect()
        row = connection.execute("SELECT COUNT(*) AS n FROM embedding_records").fetchone()
        return int(row["n"]) if row else 0

    def _get_meta(self, key: str) -> Any:
        connection = self.connect()
        row = connection.execute(
            "SELECT value_json FROM index_metadata WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            raise IndexNotReadyError(f"missing index metadata key: {key}")
        return json.loads(row["value_json"])

    def get_chunk(self, chunk_id: str) -> RetrievalChunk:
        connection = self.connect()
        row = connection.execute(
            "SELECT * FROM chunks WHERE chunk_id = ?",
            (chunk_id,),
        ).fetchone()
        if row is None:
            raise KeyError(chunk_id)
        return _row_to_chunk(row)

    def get_chunks(self, chunk_ids: Sequence[str]) -> dict[str, RetrievalChunk]:
        if not chunk_ids:
            return {}
        connection = self.connect()
        placeholders = ",".join("?" for _ in chunk_ids)
        rows = connection.execute(
            f"SELECT * FROM chunks WHERE chunk_id IN ({placeholders})",
            tuple(chunk_ids),
        ).fetchall()
        return {row["chunk_id"]: _row_to_chunk(row) for row in rows}

    def filtered_chunk_ids(self, filters: RetrievalFilters) -> set[str] | None:
        """Return matching chunk IDs, or ``None`` when no hard filters are set."""
        where, params = build_filter_sql(filters, alias="cm")
        if not where:
            return None
        connection = self.connect()
        sql = f"SELECT cm.chunk_id FROM chunk_metadata AS cm WHERE {where}"
        rows = connection.execute(sql, params).fetchall()
        return {row["chunk_id"] for row in rows}

    def iter_filtered_embeddings(
        self,
        filters: RetrievalFilters,
    ) -> list[tuple[str, list[float]]]:
        """Load embedding vectors for chunks that pass hard filters (before scoring)."""
        where, params = build_filter_sql(filters, alias="cm")
        connection = self.connect()
        if where:
            sql = f"""
                SELECT er.chunk_id, er.dimension, er.vector_blob, er.vector_checksum
                FROM embedding_records AS er
                INNER JOIN chunk_metadata AS cm ON cm.chunk_id = er.chunk_id
                WHERE {where}
            """
            rows = connection.execute(sql, params).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT chunk_id, dimension, vector_blob, vector_checksum
                FROM embedding_records
                """
            ).fetchall()
        results: list[tuple[str, list[float]]] = []
        for row in rows:
            vector = unpack_float32_vector(
                bytes(row["vector_blob"]),
                dimension=int(row["dimension"]),
                checksum=str(row["vector_checksum"]),
            )
            results.append((str(row["chunk_id"]), vector))
        return results


def build_filter_sql(
    filters: RetrievalFilters,
    *,
    alias: str = "cm",
) -> tuple[str, list[Any]]:
    """Build AND-of-fields / OR-within-field SQL for hard filters."""
    clauses: list[str] = []
    params: list[Any] = []

    def _in_clause(column: str, values: Sequence[str | int]) -> None:
        if not values:
            return
        placeholders = ",".join("?" for _ in values)
        clauses.append(f"{alias}.{column} IN ({placeholders})")
        params.extend(values)

    _in_clause("document_id", filters.document_ids)
    _in_clause("document_version_id", filters.document_version_ids)
    _in_clause("artifact_id", filters.artifact_ids)
    _in_clause("source_file", filters.source_files)
    if filters.chunk_kinds:
        _in_clause("kind", [kind.value for kind in filters.chunk_kinds])
    if filters.chunk_levels:
        _in_clause("level", [level.value for level in filters.chunk_levels])
    if filters.page_numbers:
        placeholders = ",".join("?" for _ in filters.page_numbers)
        clauses.append(
            f"""EXISTS (
                SELECT 1 FROM json_each({alias}.page_numbers_json) AS pages
                WHERE CAST(pages.value AS INTEGER) IN ({placeholders})
            )"""
        )
        params.extend(filters.page_numbers)

    if not clauses:
        return "", []
    return " AND ".join(clauses), params


def _row_to_chunk(row: sqlite3.Row) -> RetrievalChunk:
    return RetrievalChunk.model_validate(
        {
            "id": row["chunk_id"],
            "document_id": row["document_id"],
            "document_version_id": row["document_version_id"],
            "artifact_id": row["artifact_id"],
            "source_file": row["source_file"],
            "kind": row["kind"],
            "level": row["level"],
            "page_numbers": json.loads(row["page_numbers_json"]),
            "raw_text": row["raw_text"],
            "retrieval_text": row["retrieval_text"],
            "retrieval_text_kind": row["retrieval_text_kind"],
            "parent_chunk_id": row["parent_chunk_id"],
            "source_block_ids": json.loads(row["source_block_ids_json"]),
            "source_table_ids": json.loads(row["source_table_ids_json"]),
            "evidence_span_ids": json.loads(row["evidence_span_ids_json"]),
            "heading_path": json.loads(row["heading_path_json"]),
            "ordinal": row["ordinal"],
            "character_count": row["character_count"],
            "estimated_token_count": row["estimated_token_count"],
            "metadata": json.loads(row["metadata_json"]),
        }
    )


__all__ = [
    "SqliteRetrievalStore",
    "build_filter_sql",
    "ensure_fts5_available",
]
