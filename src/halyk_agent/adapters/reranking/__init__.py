"""Reranking adapters (optional cross-encoder after hybrid RRF)."""

from __future__ import annotations

from typing import Any

from halyk_agent.adapters.reranking.errors import (
    RerankingDependencyMissingError,
    RerankingError,
    RerankingValidationError,
)

__all__ = [
    "CrossEncoderReranker",
    "RerankingDependencyMissingError",
    "RerankingError",
    "RerankingValidationError",
    "ensure_cross_encoder_available",
]


def __getattr__(name: str) -> Any:
    if name == "CrossEncoderReranker":
        from halyk_agent.adapters.reranking.cross_encoder import CrossEncoderReranker

        return CrossEncoderReranker
    if name == "ensure_cross_encoder_available":
        from halyk_agent.adapters.reranking.cross_encoder import (
            ensure_cross_encoder_available,
        )

        return ensure_cross_encoder_available
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
