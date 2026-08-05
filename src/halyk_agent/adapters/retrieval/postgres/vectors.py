"""Exact cosine vector search via pgvector (no HNSW index)."""

from __future__ import annotations

from typing import Any

from halyk_agent.adapters.retrieval.errors import EmbeddingDimensionError
from halyk_agent.adapters.retrieval.postgres.deps import ensure_postgres_available
from halyk_agent.adapters.retrieval.postgres.filters import build_filter_sql
from halyk_agent.domain.retrieval import RetrievalFilters

ensure_postgres_available()

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402


def _vector_literal(values: list[float]) -> str:
    """Format a pgvector literal; values are validated floats only."""
    if not values:
        raise EmbeddingDimensionError("query embedding must be non-empty")
    parts: list[str] = []
    for index, raw in enumerate(values):
        number = float(raw)
        if number != number or number in (float("inf"), float("-inf")):
            raise EmbeddingDimensionError(f"query embedding value at index {index} must be finite")
        parts.append(repr(number))
    return "[" + ",".join(parts) + "]"


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
    if limit < 1:
        raise ValueError("limit must be >= 1")
    if expected_dimension is not None and len(query_embedding) != expected_dimension:
        raise EmbeddingDimensionError(
            f"query embedding dimension {len(query_embedding)} "
            f"does not match index dimension {expected_dimension}"
        )

    where, filter_params = build_filter_sql(filters, alias="c")
    filter_sql = f"AND ({where})" if where else ""
    # Bind the query vector as text cast to vector to keep SQLAlchemy parameterization.
    params: dict[str, Any] = {
        "query_vector": _vector_literal(query_embedding),
        "limit": limit,
        **filter_params,
    }
    sql = text(
        f"""
        SELECT
            e.chunk_id AS chunk_id,
            1.0 - (e.embedding <=> CAST(:query_vector AS vector)) AS cosine_similarity
        FROM retrieval_embeddings AS e
        INNER JOIN retrieval_chunks AS c ON c.chunk_id = e.chunk_id
        WHERE 1 = 1
        {filter_sql}
        ORDER BY e.embedding <=> CAST(:query_vector AS vector) ASC, e.chunk_id ASC
        LIMIT :limit
        """
    )
    result = await session.execute(sql, params)
    rows = result.mappings().all()
    return [(str(row["chunk_id"]), float(row["cosine_similarity"])) for row in rows]


__all__ = ["vector_search"]
