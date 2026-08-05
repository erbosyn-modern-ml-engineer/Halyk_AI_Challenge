"""Vector backend selection for PostgreSQL retrieval (Docker-free)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession


class VectorBackendName(StrEnum):
    """Named vector storage/search backends."""

    POSTGRES_NUMPY_EXACT = "postgres_numpy_exact"
    PGVECTOR = "pgvector"


async def pgvector_extension_installed(
    target: AsyncConnection | AsyncSession | AsyncEngine,
) -> bool:
    """Return True when the ``vector`` extension is already installed.

    Does not create or compile the extension.
    """
    sql = text(
        """
        SELECT extname, extversion
        FROM pg_extension
        WHERE extname = 'vector'
        """
    )
    if isinstance(target, AsyncEngine):
        async with target.connect() as connection:
            engine_result = await connection.execute(sql)
            return engine_result.first() is not None
    session_result = await target.execute(sql)
    return session_result.first() is not None


async def resolve_vector_backend(
    target: AsyncConnection | AsyncSession | AsyncEngine,
    *,
    prefer_pgvector: bool = True,
    forced: VectorBackendName | str | None = None,
) -> VectorBackendName:
    """Choose backend: optional pgvector only when extension already exists."""
    if forced is not None:
        name = VectorBackendName(str(forced))
        if name is VectorBackendName.PGVECTOR:
            installed = await pgvector_extension_installed(target)
            if not installed:
                raise PgvectorExtensionMissingError(
                    "pgvector backend requested but extension 'vector' is not installed. "
                    "Use postgres_numpy_exact (Docker-free default) or install pgvector "
                    "manually on the existing PostgreSQL instance."
                )
        return name
    if prefer_pgvector and await pgvector_extension_installed(target):
        return VectorBackendName.PGVECTOR
    return VectorBackendName.POSTGRES_NUMPY_EXACT


class PgvectorExtensionMissingError(RuntimeError):
    """Raised when pgvector is requested but the extension is absent."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def backend_lexical_configuration(backend: VectorBackendName) -> dict[str, Any]:
    """IndexIdentity lexical/vector metadata fragment."""
    return {
        "lexical_backend": "postgres_simple",
        "fts_config": "simple",
        "policy": "or_lexemes",
        "vector_backend": str(backend),
    }


__all__ = [
    "PgvectorExtensionMissingError",
    "VectorBackendName",
    "backend_lexical_configuration",
    "pgvector_extension_installed",
    "resolve_vector_backend",
]
