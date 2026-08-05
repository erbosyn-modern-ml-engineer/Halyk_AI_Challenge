"""Unit tests for PostgreSQL filter SQL and RRF parity (no database)."""

from __future__ import annotations

import pytest

from halyk_agent.adapters.retrieval.postgres.filters import build_filter_sql
from halyk_agent.adapters.retrieval.rrf import reciprocal_rank_fusion
from halyk_agent.domain.chunking import ChunkKind, ChunkLevel
from halyk_agent.domain.retrieval import RetrievalFilters


def test_build_filter_sql_empty() -> None:
    where, params = build_filter_sql(RetrievalFilters())
    assert where == ""
    assert params == {}


def test_build_filter_sql_and_of_fields_or_within_field() -> None:
    filters = RetrievalFilters(
        document_ids=["doc-b", "doc-a"],
        chunk_kinds=[ChunkKind.TABLE, ChunkKind.TEXT],
        chunk_levels=[ChunkLevel.ATOMIC],
        page_numbers=[3, 1],
    )
    where, params = build_filter_sql(filters, alias="c")
    assert "c.document_id = ANY(:document_ids)" in where
    assert "c.kind = ANY(:chunk_kinds)" in where
    assert "c.level = ANY(:chunk_levels)" in where
    assert "jsonb_array_elements_text(c.page_numbers)" in where
    assert " AND " in where
    # Domain model sorts/uniques filter lists.
    assert params["document_ids"] == ["doc-a", "doc-b"]
    assert params["chunk_kinds"] == ["TABLE", "TEXT"]
    assert params["chunk_levels"] == ["ATOMIC"]
    assert params["page_numbers"] == [1, 3]
    # Values are bind params only — not interpolated into SQL.
    assert "doc-a" not in where
    assert "TABLE" not in where


def test_rrf_parity_with_fast_shared_ranked_lists() -> None:
    """FULL and FAST share adapters.retrieval.rrf — identical fusion results."""
    lexical = ["c-ru", "c-en", "c-kk"]
    vector = ["c-en", "c-ru", "c-other"]
    fused = reciprocal_rank_fusion([lexical, vector], rrf_k=60)
    score_ru = 1.0 / (60 + 1) + 1.0 / (60 + 2)
    score_en = 1.0 / (60 + 2) + 1.0 / (60 + 1)
    by_id = {chunk_id: score for chunk_id, score, _ in fused}
    assert by_id["c-ru"] == pytest.approx(score_ru)
    assert by_id["c-en"] == pytest.approx(score_en)
    # Equal component scores → deterministic chunk_id ascending tie-break.
    assert [chunk_id for chunk_id, _, _ in fused[:2]] == ["c-en", "c-ru"]
