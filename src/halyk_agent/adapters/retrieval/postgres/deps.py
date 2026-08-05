"""Lazy dependency checks for FULL PostgreSQL retrieval."""

from __future__ import annotations

from halyk_agent.adapters.retrieval.errors import PostgresDependencyMissingError


def ensure_postgres_available() -> None:
    """Raise when core PostgreSQL retrieval deps are missing (no Docker required).

    ``pgvector`` Python package is optional — required only for the pgvector backend.
    """
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
        import numpy  # noqa: F401
    except ImportError:
        missing.append("numpy")
    if missing:
        raise PostgresDependencyMissingError(
            "PostgreSQL retrieval dependencies are not installed "
            f"(missing: {', '.join(missing)}). "
            "Install with: uv sync --extra retrieval-full"
        )


def ensure_pgvector_package() -> None:
    """Raise when the optional pgvector Python package is missing."""
    ensure_postgres_available()
    try:
        import pgvector  # noqa: F401
    except ImportError as exc:
        raise PostgresDependencyMissingError(
            "pgvector Python package is not installed. "
            "Install with: uv sync --extra retrieval-full "
            "(extension must already exist on the PostgreSQL instance; "
            "this project never installs/compiles pgvector automatically)."
        ) from exc


__all__ = ["ensure_pgvector_package", "ensure_postgres_available"]
