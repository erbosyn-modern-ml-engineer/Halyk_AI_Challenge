"""PostgreSQL FTS lexical retrieval (simple config for mixed KK/RU/EN)."""

from __future__ import annotations

from typing import Any

from halyk_agent.adapters.retrieval.postgres.deps import ensure_postgres_available
from halyk_agent.adapters.retrieval.postgres.filters import build_filter_sql
from halyk_agent.adapters.retrieval.postgres.models import FTS_CONFIG
from halyk_agent.domain.retrieval import RetrievalFilters

ensure_postgres_available()

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402


async def lexical_search(
    session: AsyncSession,
    *,
    query_text: str,
    filters: RetrievalFilters,
    limit: int,
) -> list[tuple[str, float]]:
    """Return ``(chunk_id, ts_rank)`` ordered best-first under hard filters.

    Uses ``to_tsvector('simple', retrieval_text)`` / ``plainto_tsquery('simple', ...)``
    so mixed Kazakh / Russian / English tokens are not English-stemmed away.
    """
    if limit < 1:
        raise ValueError("limit must be >= 1")
    stripped = query_text.strip()
    if not stripped:
        raise ValueError("FTS query must contain at least one token")

    where, filter_params = build_filter_sql(filters, alias="c")
    filter_sql = f"AND ({where})" if where else ""
    params: dict[str, Any] = {
        "query_text": stripped,
        "limit": limit,
        **filter_params,
    }
    sql = text(
        f"""
        SELECT
            c.chunk_id AS chunk_id,
            ts_rank_cd(
                to_tsvector('{FTS_CONFIG}', c.retrieval_text),
                plainto_tsquery('{FTS_CONFIG}', :query_text)
            ) AS rank_score
        FROM retrieval_chunks AS c
        WHERE to_tsvector('{FTS_CONFIG}', c.retrieval_text)
              @@ plainto_tsquery('{FTS_CONFIG}', :query_text)
        {filter_sql}
        ORDER BY rank_score DESC, c.chunk_id ASC
        LIMIT :limit
        """
    )
    result = await session.execute(sql, params)
    rows = result.mappings().all()
    return [(str(row["chunk_id"]), float(row["rank_score"])) for row in rows]


__all__ = ["lexical_search"]
