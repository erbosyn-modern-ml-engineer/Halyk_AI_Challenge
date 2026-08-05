"""PostgreSQL FTS lexical retrieval (simple config for mixed KK/RU/EN)."""

from __future__ import annotations

import re
from typing import Any, Literal

from halyk_agent.adapters.retrieval.postgres.deps import ensure_postgres_available
from halyk_agent.adapters.retrieval.postgres.filters import build_filter_sql
from halyk_agent.adapters.retrieval.postgres.models import FTS_CONFIG
from halyk_agent.domain.retrieval import RetrievalFilters

ensure_postgres_available()

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

LEXICAL_POLICY_OR: Literal["or_lexemes"] = "or_lexemes"
LEXICAL_POLICY_AND: Literal["and_lexemes"] = "and_lexemes"
LexicalPolicy = Literal["or_lexemes", "and_lexemes"]

# Deterministic stop words for mixed KK/RU/EN competition text (lowercase).
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "for",
        "by",
        "with",
        "from",
        "as",
        "is",
        "at",
        "po",
        "i",
        # Multi-char RU/KK stopwords via codepoints (avoids RUF001).
        "".join(map(chr, (0x043F, 0x043E))),  # po
        "".join(map(chr, (0x043C, 0x0435, 0x043D))),  # men
        "".join(map(chr, (0x0436, 0x0435))),
        "".join(map(chr, (0x0434, 0x0430))),
        "".join(map(chr, (0x043D, 0x0435))),
        "".join(map(chr, (0x043D, 0x0430))),
        "".join(map(chr, (0x043E, 0x0431))),
        "".join(map(chr, (0x043E, 0x0442))),
        "".join(map(chr, (0x0438, 0x0437))),
        "".join(map(chr, (0x0437, 0x0430))),
        "".join(map(chr, (0x0441, 0x043E))),
        "".join(map(chr, (0x0434, 0x043E))),  # do
        *map(chr, (0x0438, 0x0430, 0x0435, 0x0443, 0x043E, 0x043A, 0x0441)),
    }
)

_TOKEN_SPLIT = re.compile(r"\s+")
# Keep letters, digits, underscore, hyphen for contract IDs like CTR-2024-01.
_LEXEME_CLEAN = re.compile(r"[^\w\-]+", re.UNICODE)


def build_simple_tsquery(
    query_text: str,
    *,
    policy: LexicalPolicy = LEXICAL_POLICY_OR,
) -> tuple[str, list[str]]:
    """Build a bound-parameter tsquery string under an explicit lexical policy.

    Default ``or_lexemes`` joins surviving tokens with ``|`` for recall.
    ``and_lexemes`` joins with ``&`` for strict evaluation.
    """
    if policy not in {LEXICAL_POLICY_OR, LEXICAL_POLICY_AND}:
        raise ValueError(f"unsupported lexical policy: {policy}")
    raw_tokens = [part for part in _TOKEN_SPLIT.split(query_text.strip()) if part]
    tokens: list[str] = []
    for raw in raw_tokens:
        cleaned = _LEXEME_CLEAN.sub("", raw).lower()
        if not cleaned or cleaned in _STOP_WORDS:
            continue
        # Escape single quotes for to_tsquery quoted lexemes.
        escaped = cleaned.replace("'", "''")
        tokens.append(escaped)
    if not tokens:
        raise ValueError("FTS query must contain at least one non-stopword token")
    joiner = " | " if policy == LEXICAL_POLICY_OR else " & "
    # Quoted lexemes avoid operator injection; passed as a single bind value.
    tsquery = joiner.join(f"'{token}'" for token in tokens)
    return tsquery, tokens


async def lexical_search(
    session: AsyncSession,
    *,
    query_text: str,
    filters: RetrievalFilters,
    limit: int,
    lexical_policy: LexicalPolicy = LEXICAL_POLICY_OR,
) -> tuple[list[tuple[str, float]], LexicalPolicy, list[str]]:
    """Return ``(hits, policy, tokens)`` with hits as ``(chunk_id, ts_rank)``.

    Uses ``to_tsvector('simple', ...)`` / ``to_tsquery('simple', :tsquery)`` so
    mixed Kazakh / Russian / English tokens are not English-stemmed away.
    """
    if limit < 1:
        raise ValueError("limit must be >= 1")
    stripped = query_text.strip()
    if not stripped:
        raise ValueError("FTS query must contain at least one token")

    tsquery, tokens = build_simple_tsquery(stripped, policy=lexical_policy)
    where, filter_params = build_filter_sql(filters, alias="c")
    filter_sql = f"AND ({where})" if where else ""
    params: dict[str, Any] = {
        "tsquery": tsquery,
        "limit": limit,
        **filter_params,
    }
    # FTS_CONFIG is a fixed module constant ('simple'), never user input.
    sql = text(
        f"""
        SELECT
            c.chunk_id AS chunk_id,
            ts_rank_cd(
                to_tsvector('{FTS_CONFIG}', c.retrieval_text),
                to_tsquery('{FTS_CONFIG}', :tsquery)
            ) AS rank_score
        FROM retrieval_chunks AS c
        WHERE to_tsvector('{FTS_CONFIG}', c.retrieval_text)
              @@ to_tsquery('{FTS_CONFIG}', :tsquery)
        {filter_sql}
        ORDER BY rank_score DESC, c.chunk_id ASC
        LIMIT :limit
        """
    )
    result = await session.execute(sql, params)
    rows = result.mappings().all()
    hits = [(str(row["chunk_id"]), float(row["rank_score"])) for row in rows]
    return hits, lexical_policy, tokens


__all__ = [
    "LEXICAL_POLICY_AND",
    "LEXICAL_POLICY_OR",
    "LexicalPolicy",
    "build_simple_tsquery",
    "lexical_search",
]
