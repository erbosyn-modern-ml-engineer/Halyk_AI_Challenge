"""Lazy dependency checks for FULL PostgreSQL retrieval."""

from __future__ import annotations

from halyk_agent.adapters.retrieval.errors import PostgresDependencyMissingError


def ensure_postgres_available() -> None:
    """Raise a typed error when retrieval-full dependencies are missing."""
    missing: list[str] = []
    try:
        import sqlalchemy  # noqa: F401
    except ImportError:
        missing.append("sqlalchemy")
    try:
        import asyncpg  # noqa: F401
    except ImportError:
        missing.append("asyncpg")
    try:
        import pgvector  # noqa: F401
    except ImportError:
        missing.append("pgvector")
    if missing:
        raise PostgresDependencyMissingError(
            "PostgreSQL retrieval dependencies are not installed "
            f"(missing: {', '.join(missing)}). "
            "Install with: uv sync --extra retrieval-full"
        )


__all__ = ["ensure_postgres_available"]
