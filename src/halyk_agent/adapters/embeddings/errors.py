"""Typed embedding-adapter errors (no secret/path leakage)."""

from __future__ import annotations


class EmbeddingError(Exception):
    """Base class for embedding failures."""

    def __init__(self, message: str, *, code: str = "EMBEDDING_ERROR") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class EmbeddingDependencyMissingError(EmbeddingError):
    """Raised when an optional embedding dependency is not installed."""

    def __init__(self, message: str = "embedding dependency missing") -> None:
        super().__init__(message, code="DEPENDENCY_MISSING")


class EmbeddingValidationError(EmbeddingError):
    """Raised when an embedding vector fails validation (dim / NaN / Inf)."""

    def __init__(self, message: str = "embedding validation failed") -> None:
        super().__init__(message, code="EMBEDDING_INVALID")


class EmbeddingTruncationError(EmbeddingError):
    """Raised when input would be truncated and rejection is configured."""

    def __init__(self, message: str = "embedding input truncated") -> None:
        super().__init__(message, code="EMBEDDING_TRUNCATED")


class EmbeddingModelNotFoundError(EmbeddingError):
    """Raised when a logical model name is missing from the model lock."""

    def __init__(self, message: str = "embedding model not found") -> None:
        super().__init__(message, code="MODEL_NOT_FOUND")


class EmbeddingCacheError(EmbeddingError):
    """Raised for prohibited or corrupt embedding-cache operations."""

    def __init__(self, message: str = "embedding cache error") -> None:
        super().__init__(message, code="CACHE_ERROR")


__all__ = [
    "EmbeddingCacheError",
    "EmbeddingDependencyMissingError",
    "EmbeddingError",
    "EmbeddingModelNotFoundError",
    "EmbeddingTruncationError",
    "EmbeddingValidationError",
]
