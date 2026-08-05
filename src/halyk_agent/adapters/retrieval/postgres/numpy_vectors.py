"""Exact cosine vector search via NumPy over BYTEA float32 embeddings.

Backend name: ``postgres_numpy_exact``.

Hard metadata filters are applied in PostgreSQL first; bounded candidate vectors
are scored with exact cosine similarity in process (no approximate ANN).
"""

from __future__ import annotations

from typing import Any

from halyk_agent.adapters.retrieval.errors import EmbeddingDimensionError
from halyk_agent.adapters.retrieval.local.vectors import (
    brute_force_cosine_topk,
    unpack_float32_vector,
)
from halyk_agent.adapters.retrieval.postgres.deps import ensure_postgres_available
from halyk_agent.adapters.retrieval.postgres.filters import build_filter_sql
from halyk_agent.domain.retrieval import RetrievalFilters

ensure_postgres_available()

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

VECTOR_BACKEND_NAME = "postgres_numpy_exact"


async def numpy_exact_vector_search(
    session: AsyncSession,
    *,
    query_embedding: list[float],
    filters: RetrievalFilters,
    limit: int,
    expected_dimension: int | None = None,
) -> list[tuple[str, float]]:
    """Filter in SQL, then exact cosine in NumPy/Python over BYTEA vectors."""
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
    params: dict[str, Any] = dict(filter_params)
    sql = text(
        f"""
        SELECT
            e.chunk_id AS chunk_id,
            e.dimension AS dimension,
            e.embedding_blob AS embedding_blob,
            e.vector_checksum AS vector_checksum
        FROM retrieval_embeddings AS e
        INNER JOIN retrieval_chunks AS c ON c.chunk_id = e.chunk_id
        WHERE 1 = 1
        {filter_sql}
        ORDER BY e.chunk_id ASC
        """
    )
    result = await session.execute(sql, params)
    rows = result.mappings().all()

    candidates: list[tuple[str, list[float]]] = []
    for row in rows:
        dimension = int(row["dimension"])
        if expected_dimension is not None and dimension != expected_dimension:
            raise EmbeddingDimensionError(
                f"stored embedding dimension {dimension} "
                f"does not match index dimension {expected_dimension}"
            )
        values = unpack_float32_vector(
            bytes(row["embedding_blob"]),
            dimension=dimension,
            checksum=str(row["vector_checksum"]),
        )
        candidates.append((str(row["chunk_id"]), values))

    return brute_force_cosine_topk(query_embedding, candidates, top_k=limit)


__all__ = ["VECTOR_BACKEND_NAME", "numpy_exact_vector_search"]
