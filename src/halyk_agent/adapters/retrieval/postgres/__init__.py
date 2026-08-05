"""FULL PostgreSQL hybrid retrieval (FTS + exact vectors + RRF)."""

from __future__ import annotations

from typing import Any

from halyk_agent.adapters.retrieval.postgres.filters import build_filter_sql

__all__ = [
    "PostgresHybridRetriever",
    "build_filter_sql",
]


def __getattr__(name: str) -> Any:
    if name == "PostgresHybridRetriever":
        from halyk_agent.adapters.retrieval.postgres.hybrid import PostgresHybridRetriever

        return PostgresHybridRetriever
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
