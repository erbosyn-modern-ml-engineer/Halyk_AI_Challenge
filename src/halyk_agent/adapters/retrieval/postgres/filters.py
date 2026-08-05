"""Pure SQL filter builder for PostgreSQL hard filters (no DB required)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from halyk_agent.domain.retrieval import RetrievalFilters


def build_filter_sql(
    filters: RetrievalFilters,
    *,
    alias: str = "c",
) -> tuple[str, dict[str, Any]]:
    """Build AND-of-fields / OR-within-field SQL with named bind parameters.

    Returns:
        ``(where_sql, params)`` where ``where_sql`` is empty when no filters apply.
        Callers prepend ``AND`` / ``WHERE`` as needed. Parameterized only (no f-string
        interpolation of user values).
    """
    clauses: list[str] = []
    params: dict[str, Any] = {}

    def _any_clause(column: str, values: Sequence[str | int], param_name: str) -> None:
        if not values:
            return
        clauses.append(f"{alias}.{column} = ANY(:{param_name})")
        params[param_name] = list(values)

    _any_clause("document_id", filters.document_ids, "document_ids")
    _any_clause("document_version_id", filters.document_version_ids, "document_version_ids")
    _any_clause("artifact_id", filters.artifact_ids, "artifact_ids")
    _any_clause("source_file", filters.source_files, "source_files")
    if filters.chunk_kinds:
        _any_clause("kind", [kind.value for kind in filters.chunk_kinds], "chunk_kinds")
    if filters.chunk_levels:
        _any_clause(
            "level",
            [level.value for level in filters.chunk_levels],
            "chunk_levels",
        )
    if filters.page_numbers:
        # Hard filter on JSONB page array before ranking.
        clauses.append(
            f"""EXISTS (
                SELECT 1
                FROM jsonb_array_elements_text({alias}.page_numbers) AS pages(value)
                WHERE pages.value::integer = ANY(:page_numbers)
            )"""
        )
        params["page_numbers"] = list(filters.page_numbers)

    if not clauses:
        return "", {}
    return " AND ".join(clauses), params


__all__ = ["build_filter_sql"]
