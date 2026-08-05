"""Exact cosine vector search via pgvector (optional; no HNSW index).

Requires the PostgreSQL ``vector`` extension to already be installed.
Does not create or compile the extension.
"""

from __future__ import annotations

from typing import Any

from halyk_agent.adapters.retrieval.errors import EmbeddingDimensionError
from halyk_agent.adapters.retrieval.postgres.backend import (
    PgvectorExtensionMissingError,
    pgvector_extension_installed,
)
from halyk_agent.adapters.retrieval.postgres.deps import ensure_pgvector_package
from halyk_agent.adapters.retrieval.postgres.filters import build_filter_sql
from halyk_agent.domain.retrieval import RetrievalFilters

ensure_pgvector_package()

from sqlalchemy import bindparam, text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from halyk_agent.adapters.retrieval.postgres.models_pgvector import Vector  # noqa: E402

VECTOR_BACKEND_NAME = "pgvector"


async def vector_search(
    session: AsyncSession,
    *,
    query_embedding: list[float],
    filters: RetrievalFilters,
    limit: int,
    expected_dimension: int | None = None,
) -> list[tuple[str, float]]:
    """Exact cosine search with hard filters applied in SQL before ranking.

    Uses pgvector cosine distance (``<=>``). Similarity returned as
    ``1 - distance``. No approximate (HNSW/IVFFlat) index is created or required.
    """
    if not await pgvector_extension_installed(session):
        raise PgvectorExtensionMissingError(
            "pgvector backend requires extension 'vector' to be already installed. "
            "It was not found. Use vector backend postgres_numpy_exact instead "
            "(Docker-free default). This project never installs pgvector automatically."
        )
    if limit < 1:
        raise ValueError("limit must be >= 1")
    if not query_embedding:
        raise EmbeddingDimensionError("query embedding must be non-empty")
    for index, raw in enumerate(query_embedding):
        number = float(raw)
        if number != number or number in (float("inf"), float("-inf")):
            raise EmbeddingDimensionError(f"query embedding value at index {index} must be finite")
    if expected_dimension is not None and len(query_embedding) != expected_dimension:
        raise EmbeddingDimensionError(
            f"query embedding dimension {len(query_embedding)} "
            f"does not match index dimension {expected_dimension}"
        )

    where, filter_params = build_filter_sql(filters, alias="c")
    filter_sql = f"AND ({where})" if where else ""
    params: dict[str, Any] = {
        "query_vector": [float(item) for item in query_embedding],
        "limit": limit,
        **filter_params,
    }
    sql = text(
        f"""
        SELECT
            e.chunk_id AS chunk_id,
            1.0 - (e.embedding <=> :query_vector) AS cosine_similarity
        FROM retrieval_embeddings AS e
        INNER JOIN retrieval_chunks AS c ON c.chunk_id = e.chunk_id
        WHERE 1 = 1
        {filter_sql}
        ORDER BY e.embedding <=> :query_vector ASC, e.chunk_id ASC
        LIMIT :limit
        """
    ).bindparams(bindparam("query_vector", type_=Vector()))
    result = await session.execute(sql, params)
    rows = result.mappings().all()
    return [(str(row["chunk_id"]), float(row["cosine_similarity"])) for row in rows]


__all__ = ["VECTOR_BACKEND_NAME", "vector_search"]
