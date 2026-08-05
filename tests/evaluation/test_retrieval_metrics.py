"""Deterministic retrieval metric tests (no LLM judge, no model downloads)."""

from __future__ import annotations

import math

import pytest

from halyk_agent.evaluation.retrieval import (
    LabeledQuery,
    RetrievalMode,
    evaluate_mode,
    evaluate_ranking,
    hit_rate_at_k,
    mean_metrics,
    mrr,
    multilingual_fixture_queries,
    ndcg_at_k,
    recall_at_k,
)


def test_recall_at_k_partial_and_full() -> None:
    relevant = {"a", "b", "c"}
    ranked = ["x", "a", "b", "y", "c"]
    assert recall_at_k(relevant, ranked, k=2) == pytest.approx(1 / 3)
    assert recall_at_k(relevant, ranked, k=5) == pytest.approx(1.0)
    assert recall_at_k(relevant, ranked, k=1) == pytest.approx(0.0)


def test_mrr_first_relevant_rank() -> None:
    relevant = {"target"}
    assert mrr(relevant, ["a", "target", "b"]) == pytest.approx(0.5)
    assert mrr(relevant, ["target"]) == pytest.approx(1.0)
    assert mrr(relevant, ["a", "b"]) == pytest.approx(0.0)


def test_hit_rate_at_k() -> None:
    relevant = {"hit"}
    assert hit_rate_at_k(relevant, ["miss", "hit"], k=1) == 0.0
    assert hit_rate_at_k(relevant, ["miss", "hit"], k=2) == 1.0


def test_ndcg_at_k_binary_perfect_and_imperfect() -> None:
    grades = {"a": 1.0, "b": 1.0}
    perfect = ndcg_at_k(grades, ["a", "b", "c"], k=2)
    imperfect = ndcg_at_k(grades, ["c", "a", "b"], k=2)
    assert perfect == pytest.approx(1.0)
    assert 0.0 < imperfect < 1.0


def test_ndcg_graded_relevance() -> None:
    grades = {"best": 3.0, "ok": 1.0}
    assert ndcg_at_k(grades, ["best", "ok"], k=2) == pytest.approx(1.0)
    lower = ndcg_at_k(grades, ["ok", "best"], k=2)
    assert 0.0 < lower < 1.0


def test_evaluate_ranking_bundle() -> None:
    metrics = evaluate_ranking({"doc1"}, ["noise", "doc1"], k=2)
    assert metrics.k == 2
    assert metrics.recall_at_k == pytest.approx(1.0)
    assert metrics.mrr == pytest.approx(0.5)
    assert metrics.hit_rate_at_k == pytest.approx(1.0)
    assert metrics.ndcg_at_k > 0.0


def test_mean_metrics_macro_average() -> None:
    a = evaluate_ranking({"a"}, ["a"], k=1)
    b = evaluate_ranking({"b"}, ["x"], k=1)
    averaged = mean_metrics([a, b])
    assert averaged.recall_at_k == pytest.approx(0.5)
    assert averaged.hit_rate_at_k == pytest.approx(0.5)
    assert averaged.mrr == pytest.approx(0.5)


def test_evaluate_mode_reports_separately() -> None:
    labeled = [
        LabeledQuery("q1", "query one", frozenset({"c1"})),
        LabeledQuery("q2", "query two", frozenset({"c2"})),
    ]
    lexical = evaluate_mode(
        labeled,
        {"q1": ["c1"], "q2": ["noise"]},
        k=1,
        mode=RetrievalMode.LEXICAL,
    )
    hybrid = evaluate_mode(
        labeled,
        {"q1": ["c1"], "q2": ["c2"]},
        k=1,
        mode=RetrievalMode.HYBRID_RRF,
    )
    assert lexical["mode"] == "lexical-only"
    assert hybrid["mode"] == "hybrid-rrf"
    assert lexical["hit_rate_at_k"] == pytest.approx(0.5)
    assert hybrid["hit_rate_at_k"] == pytest.approx(1.0)
    assert hybrid["recall_at_k"] == pytest.approx(1.0)


def test_multilingual_fixture_covers_required_languages() -> None:
    queries = multilingual_fixture_queries()
    languages = {item.language for item in queries}
    assert {"kk", "ru", "en", "mixed"} <= languages
    notes = " ".join(item.notes.lower() for item in queries)
    assert "contract" in notes or "invoice" in notes
    assert "amount" in notes or "currency" in notes
    assert "table" in notes
    assert all(item.relevant_ids for item in queries)


def test_metrics_reject_invalid_k() -> None:
    with pytest.raises(ValueError):
        recall_at_k({"a"}, ["a"], k=0)
    with pytest.raises(ValueError):
        ndcg_at_k({"a": 1.0}, ["a"], k=0)


def test_ndcg_empty_grades() -> None:
    assert ndcg_at_k({}, ["a"], k=1) == 0.0
    assert math.isclose(mrr(set(), ["a"]), 0.0)
