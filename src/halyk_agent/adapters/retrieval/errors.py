"""Typed retrieval adapter errors (no secret/path leakage)."""

from __future__ import annotations


class RetrievalError(Exception):
    """Base class for retrieval adapter failures."""

    def __init__(self, message: str, *, code: str = "RETRIEVAL_ERROR") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class Fts5UnavailableError(RetrievalError):
    """Raised when SQLite was built without FTS5 support."""

    def __init__(self, message: str = "SQLite FTS5 is unavailable") -> None:
        super().__init__(message, code="FTS5_UNAVAILABLE")


class CorruptEmbeddingBlobError(RetrievalError):
    """Raised when a stored embedding BLOB fails checksum or length checks."""

    def __init__(self, message: str = "corrupt embedding blob") -> None:
        super().__init__(message, code="CORRUPT_EMBEDDING_BLOB")


class EmbeddingDimensionError(RetrievalError):
    """Raised when vector dimensions do not match the index model."""

    def __init__(self, message: str = "embedding dimension mismatch") -> None:
        super().__init__(message, code="EMBEDDING_DIMENSION")


class HybridUnavailableError(RetrievalError):
    """Raised when hybrid search is requested but cannot run without downgrade."""

    def __init__(
        self,
        message: str = "hybrid search unavailable without silent downgrade",
    ) -> None:
        super().__init__(message, code="HYBRID_UNAVAILABLE")


class IndexNotReadyError(RetrievalError):
    """Raised when the local index is missing or not marked ready."""

    def __init__(self, message: str = "local retrieval index is not ready") -> None:
        super().__init__(message, code="INDEX_NOT_READY")


class PostgresDependencyMissingError(RetrievalError):
    """Raised when optional PostgreSQL retrieval dependencies are not installed."""

    def __init__(
        self,
        message: str = (
            "PostgreSQL retrieval dependencies are not installed. "
            "Install with: uv sync --extra retrieval-full"
        ),
    ) -> None:
        super().__init__(message, code="DEPENDENCY_MISSING")


__all__ = [
    "CorruptEmbeddingBlobError",
    "EmbeddingDimensionError",
    "Fts5UnavailableError",
    "HybridUnavailableError",
    "IndexNotReadyError",
    "PostgresDependencyMissingError",
    "RetrievalError",
]
