"""Pure reciprocal rank fusion for hybrid retrieval.

Adapts the RRF scoring idea from:
https://github.com/pgvector/pgvector-python/blob/60739dfd6cb9d674f32afa4184d43e6aff9dfbcf/examples/hybrid_search/rrf.py

Modifications versus the upstream example:
* pure in-memory fusion over ranked chunk-id lists (no SQL / DB connection code);
* returns per-list 1-based ranks alongside the fused score;
* deterministic tie-break by chunk_id ascending when scores are equal.
"""

from __future__ import annotations


def reciprocal_rank_fusion(
    ranked_lists: list[list[str]],
    *,
    rrf_k: int = 60,
) -> list[tuple[str, float, dict[str, int]]]:
    """Fuse ranked chunk-id lists with reciprocal rank fusion.

    Args:
        ranked_lists: Each inner list is chunk ids in rank order (best first).
        rrf_k: Smoothing constant in ``1 / (rrf_k + rank)`` (ranks are 1-based).

    Returns:
        List of ``(chunk_id, rrf_score, {list_index: rank})`` sorted by score
        descending, then chunk_id ascending.
    """
    if rrf_k < 0:
        raise ValueError("rrf_k must be >= 0")

    scores: dict[str, float] = {}
    ranks_by_chunk: dict[str, dict[str, int]] = {}

    for list_index, ranked in enumerate(ranked_lists):
        seen_in_list: set[str] = set()
        for zero_based_rank, chunk_id in enumerate(ranked):
            if chunk_id in seen_in_list:
                continue
            seen_in_list.add(chunk_id)
            rank = zero_based_rank + 1
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (rrf_k + rank)
            ranks_by_chunk.setdefault(chunk_id, {})[str(list_index)] = rank

    fused = [(chunk_id, score, ranks_by_chunk[chunk_id]) for chunk_id, score in scores.items()]
    fused.sort(key=lambda item: (-item[1], item[0]))
    return fused
