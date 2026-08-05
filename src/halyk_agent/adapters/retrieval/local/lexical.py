"""SQLite FTS5 BM25 lexical retrieval (unicode61, no Porter stemming)."""

from __future__ import annotations

import re
import sqlite3

from halyk_agent.adapters.retrieval.local.sqlite_store import build_filter_sql
from halyk_agent.domain.retrieval import RetrievalFilters

_TOKEN_SPLIT = re.compile(r"\s+")


def prepare_fts_query(text: str) -> str:
    """Escape query tokens for FTS5 MATCH (quoted tokens, AND semantics)."""
    tokens = [token for token in _TOKEN_SPLIT.split(text.strip()) if token]
    if not tokens:
        raise ValueError("FTS query must contain at least one token")
    quoted: list[str] = []
    for token in tokens:
        escaped = token.replace('"', '""')
        quoted.append(f'"{escaped}"')
    return " ".join(quoted)


def lexical_search(
    connection: sqlite3.Connection,
    *,
    query_text: str,
    filters: RetrievalFilters,
    limit: int,
) -> list[tuple[str, float]]:
    """Return ``(chunk_id, bm25_score)`` ordered best-first under hard filters.

    SQLite FTS5 ``bm25()`` is typically negative; more negative means a better match.
    Scores are returned as ``-bm25`` so higher is better for callers.
    """
    if limit < 1:
        raise ValueError("limit must be >= 1")
    fts_query = prepare_fts_query(query_text)
    where, params = build_filter_sql(filters, alias="cm")
    filter_sql = f"AND ({where})" if where else ""
    sql = f"""
        SELECT chunks_fts.chunk_id AS chunk_id, bm25(chunks_fts) AS bm25_score
        FROM chunks_fts
        INNER JOIN chunk_metadata AS cm ON cm.chunk_id = chunks_fts.chunk_id
        WHERE chunks_fts MATCH ?
        {filter_sql}
        ORDER BY bm25_score ASC, chunks_fts.chunk_id ASC
        LIMIT ?
    """
    rows = connection.execute(sql, [fts_query, *params, limit]).fetchall()
    results: list[tuple[str, float]] = []
    for row in rows:
        bm25_score = float(row["bm25_score"])
        results.append((str(row["chunk_id"]), -bm25_score))
    return results


__all__ = [
    "lexical_search",
    "prepare_fts_query",
]
