"""Reciprocal rank fusion helpers."""

from __future__ import annotations


def reciprocal_rank_fusion(
    ranked_lists: list[list[str]],
    *,
    rrf_k: int = 60,
) -> list[tuple[str, float, dict[str, int]]]:
    """Merge ranked chunk lists into one stable ranking."""
    if rrf_k < 0:
        raise ValueError("rrf_k must be >= 0")

    scores: dict[str, float] = {}
    ranks_by_chunk: dict[str, dict[str, int]] = {}

    for list_index, ranked in enumerate(ranked_lists):
        seen_in_list: set[str] = set()
        for zero_based_rank, chunk_id in enumerate(ranked):
            # Duplicates inside one retriever should not give the chunk extra weight.
            if chunk_id in seen_in_list:
                continue
            seen_in_list.add(chunk_id)
            rank = zero_based_rank + 1
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (rrf_k + rank)
            ranks_by_chunk.setdefault(chunk_id, {})[str(list_index)] = rank

    fused = [(chunk_id, score, ranks_by_chunk[chunk_id]) for chunk_id, score in scores.items()]
    fused.sort(key=lambda item: (-item[1], item[0]))
    return fused
