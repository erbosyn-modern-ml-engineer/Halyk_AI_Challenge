"""Deterministic retrieval evaluation metrics (no LLM judge)."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum


class RetrievalMode(StrEnum):
    """Named retrieval modes for side-by-side evaluation reports."""

    LEXICAL = "lexical-only"
    VECTOR = "vector-only"
    HYBRID_RRF = "hybrid-rrf"
    HYBRID_RERANK = "hybrid-plus-reranker"


@dataclass(frozen=True, slots=True)
class LabeledQuery:
    """A labeled evaluation query with relevant document/chunk ids."""

    query_id: str
    query: str
    relevant_ids: frozenset[str]
    language: str = "en"
    notes: str = ""
    relevance_grades: Mapping[str, float] = field(default_factory=dict)

    def grades(self) -> dict[str, float]:
        """Binary grades from relevant_ids, overridden by explicit grades."""
        grades = {doc_id: 1.0 for doc_id in self.relevant_ids}
        grades.update({key: float(value) for key, value in self.relevance_grades.items()})
        return grades


@dataclass(frozen=True, slots=True)
class RankingMetrics:
    """Per-query ranking metrics at a fixed k."""

    recall_at_k: float
    mrr: float
    ndcg_at_k: float
    hit_rate_at_k: float
    k: int


def recall_at_k(
    relevant_ids: Iterable[str],
    ranked_ids: Sequence[str],
    k: int,
) -> float:
    """Fraction of relevant ids retrieved in the top-k (0 if no relevants)."""
    if k < 1:
        raise ValueError("k must be >= 1")
    relevant = set(relevant_ids)
    if not relevant:
        return 0.0
    hits = set(ranked_ids[:k]) & relevant
    return len(hits) / len(relevant)


def mrr(relevant_ids: Iterable[str], ranked_ids: Sequence[str]) -> float:
    """Mean reciprocal rank of the first relevant hit (single-query RR)."""
    relevant = set(relevant_ids)
    if not relevant:
        return 0.0
    for rank, doc_id in enumerate(ranked_ids, start=1):
        if doc_id in relevant:
            return 1.0 / rank
    return 0.0


def hit_rate_at_k(
    relevant_ids: Iterable[str],
    ranked_ids: Sequence[str],
    k: int,
) -> float:
    """1.0 if any relevant id appears in top-k, else 0.0."""
    if k < 1:
        raise ValueError("k must be >= 1")
    relevant = set(relevant_ids)
    if not relevant:
        return 0.0
    return 1.0 if set(ranked_ids[:k]) & relevant else 0.0


def _dcg_at_k(grades_in_rank_order: Sequence[float], k: int) -> float:
    total = 0.0
    for index, grade in enumerate(grades_in_rank_order[:k], start=1):
        if grade <= 0.0:
            continue
        total += (2.0**grade - 1.0) / math.log2(index + 1)
    return total


def ndcg_at_k(
    relevance_grades: Mapping[str, float],
    ranked_ids: Sequence[str],
    k: int,
) -> float:
    """Normalized discounted cumulative gain at k (graded or binary)."""
    if k < 1:
        raise ValueError("k must be >= 1")
    if not relevance_grades:
        return 0.0
    gained = [float(relevance_grades.get(doc_id, 0.0)) for doc_id in ranked_ids[:k]]
    dcg = _dcg_at_k(gained, k)
    ideal = sorted((float(g) for g in relevance_grades.values() if g > 0.0), reverse=True)
    idcg = _dcg_at_k(ideal, k)
    if idcg == 0.0:
        return 0.0
    return dcg / idcg


def evaluate_ranking(
    relevant_ids: Iterable[str],
    ranked_ids: Sequence[str],
    *,
    k: int,
    relevance_grades: Mapping[str, float] | None = None,
) -> RankingMetrics:
    """Compute Recall@k, MRR, nDCG@k, and HitRate@k for one ranking."""
    relevant = frozenset(relevant_ids)
    grades = (
        dict(relevance_grades)
        if relevance_grades is not None
        else {doc_id: 1.0 for doc_id in relevant}
    )
    return RankingMetrics(
        recall_at_k=recall_at_k(relevant, ranked_ids, k),
        mrr=mrr(relevant, ranked_ids),
        ndcg_at_k=ndcg_at_k(grades, ranked_ids, k),
        hit_rate_at_k=hit_rate_at_k(relevant, ranked_ids, k),
        k=k,
    )


def mean_metrics(rows: Sequence[RankingMetrics]) -> RankingMetrics:
    """Macro-average metrics across queries (requires uniform k)."""
    if not rows:
        raise ValueError("rows must be non-empty")
    k = rows[0].k
    if any(row.k != k for row in rows):
        raise ValueError("all RankingMetrics must share the same k")
    n = float(len(rows))
    return RankingMetrics(
        recall_at_k=sum(row.recall_at_k for row in rows) / n,
        mrr=sum(row.mrr for row in rows) / n,
        ndcg_at_k=sum(row.ndcg_at_k for row in rows) / n,
        hit_rate_at_k=sum(row.hit_rate_at_k for row in rows) / n,
        k=k,
    )


def evaluate_mode(
    labeled: Sequence[LabeledQuery],
    rankings: Mapping[str, Sequence[str]],
    *,
    k: int,
    mode: RetrievalMode,
) -> dict[str, float | str]:
    """Evaluate one retrieval mode; rankings keyed by query_id."""
    per_query: list[RankingMetrics] = []
    for item in labeled:
        ranked = rankings.get(item.query_id, ())
        per_query.append(
            evaluate_ranking(
                item.relevant_ids,
                ranked,
                k=k,
                relevance_grades=item.grades(),
            )
        )
    averaged = mean_metrics(per_query)
    return {
        "mode": mode.value,
        "k": float(k),
        "recall_at_k": averaged.recall_at_k,
        "mrr": averaged.mrr,
        "ndcg_at_k": averaged.ndcg_at_k,
        "hit_rate_at_k": averaged.hit_rate_at_k,
        "n_queries": float(len(per_query)),
    }


def multilingual_fixture_queries() -> list[LabeledQuery]:
    """Deterministic synthetic multilingual labels (not production quality)."""
    return [
        LabeledQuery(
            query_id="kk-contract",
            query="Келісімшарт нөмірі CTR-2024-001",
            relevant_ids=frozenset({"chunk-contract-kk"}),
            language="kk",
            notes="Kazakh contract id lookup",
        ),
        LabeledQuery(
            query_id="ru-invoice",
            query="Счёт-фактура INV-7788 сумма 150000 тенге",
            relevant_ids=frozenset({"chunk-invoice-ru"}),
            language="ru",
            notes="Russian invoice + amount/currency",
        ),
        LabeledQuery(
            query_id="en-amount",
            query="payment amount KZT 250000 under agreement AG-42",
            relevant_ids=frozenset({"chunk-amount-en"}),
            language="en",
            notes="English amount/currency terminology",
        ),
        LabeledQuery(
            query_id="mixed-id",
            query="договор MIX-KZ-99 / contract MIX-KZ-99",
            relevant_ids=frozenset({"chunk-mixed-id"}),
            language="mixed",
            notes="mixed-language identifiers",
        ),
        LabeledQuery(
            query_id="table-lookup",
            query="table row counterparty Acme LLP amount 10000",
            relevant_ids=frozenset({"chunk-table-row-1"}),
            language="en",
            notes="table lookup",
        ),
    ]


__all__ = [
    "LabeledQuery",
    "RankingMetrics",
    "RetrievalMode",
    "evaluate_mode",
    "evaluate_ranking",
    "hit_rate_at_k",
    "mean_metrics",
    "mrr",
    "multilingual_fixture_queries",
    "ndcg_at_k",
    "recall_at_k",
]
