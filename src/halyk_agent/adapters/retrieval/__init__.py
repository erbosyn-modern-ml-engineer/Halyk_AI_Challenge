"""Retrieval adapters (hybrid fusion, local and PostgreSQL indexes)."""

from __future__ import annotations

from typing import Any

from halyk_agent.adapters.retrieval.local import LocalHybridRetriever
from halyk_agent.adapters.retrieval.rrf import reciprocal_rank_fusion

__all__ = [
    "LocalHybridRetriever",
    "PostgresHybridRetriever",
    "reciprocal_rank_fusion",
]


def __getattr__(name: str) -> Any:
    if name == "PostgresHybridRetriever":
        from halyk_agent.adapters.retrieval.postgres import PostgresHybridRetriever

        return PostgresHybridRetriever
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
