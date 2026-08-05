"""Typed reranking adapter errors (no secret/path leakage)."""

from __future__ import annotations


class RerankingError(Exception):
    """Base class for reranking failures."""

    def __init__(self, message: str, *, code: str = "RERANKING_ERROR") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class RerankingDependencyMissingError(RerankingError):
    """Raised when optional reranking dependencies are not installed."""

    def __init__(
        self,
        message: str = (
            "Reranking dependencies are not installed. Install with: uv sync --extra retrieval-full"
        ),
    ) -> None:
        super().__init__(message, code="DEPENDENCY_MISSING")


class RerankingValidationError(RerankingError):
    """Raised when reranker inputs fail validation."""

    def __init__(self, message: str = "reranking validation failed") -> None:
        super().__init__(message, code="RERANKING_INVALID")


__all__ = [
    "RerankingDependencyMissingError",
    "RerankingError",
    "RerankingValidationError",
]
