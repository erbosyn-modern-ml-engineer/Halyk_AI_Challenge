"""Cross-encoder reranker tests (disabled path avoids model import)."""

from __future__ import annotations

import sys

import pytest

from halyk_agent.adapters.reranking.cross_encoder import CrossEncoderReranker
from halyk_agent.domain.chunking import (
    ChunkKind,
    ChunkLevel,
    RetrievalChunk,
    RetrievalTextKind,
)
from halyk_agent.domain.retrieval import MatchedBy, RetrievalHit


def _chunk(chunk_id: str, text: str) -> RetrievalChunk:
    return RetrievalChunk(
        id=chunk_id,
        document_id="doc-1",
        document_version_id="doc-1-v1",
        artifact_id="art-1",
        source_file="doc.pdf",
        kind=ChunkKind.TEXT,
        level=ChunkLevel.ATOMIC,
        page_numbers=[1],
        raw_text=text,
        retrieval_text=text,
        retrieval_text_kind=RetrievalTextKind.RAW_SOURCE,
        evidence_span_ids=[f"span-{chunk_id}"],
        ordinal=0,
        character_count=len(text),
        estimated_token_count=1,
    )


def _hit(chunk_id: str, text: str, *, rrf: float, rank: int) -> RetrievalHit:
    return RetrievalHit(
        chunk=_chunk(chunk_id, text),
        rrf_score=rrf,
        final_rank=rank,
        matched_by=MatchedBy.HYBRID,
    )


@pytest.mark.asyncio
async def test_disabled_reranker_avoids_model_import() -> None:
    before = {
        name
        for name in sys.modules
        if name == "sentence_transformers" or name.startswith("sentence_transformers.")
    }
    reranker = CrossEncoderReranker(enabled=False)
    assert reranker.enabled is False
    assert reranker.identity() is None

    hits = [
        _hit("b", "second", rrf=0.02, rank=2),
        _hit("a", "first", rrf=0.03, rank=1),
    ]
    out = await reranker.rerank("query", hits, top_k=1)
    assert len(out) == 1
    assert out[0].chunk.id == "b"
    assert out[0].rrf_score == pytest.approx(0.02)
    assert out[0].rerank_score is None
    assert out[0].matched_by is MatchedBy.HYBRID

    await reranker.prewarm()
    after = {
        name
        for name in sys.modules
        if name == "sentence_transformers" or name.startswith("sentence_transformers.")
    }
    assert after == before


@pytest.mark.asyncio
async def test_enabled_rerank_retains_rrf_scores(monkeypatch: pytest.MonkeyPatch) -> None:
    from halyk_agent.domain.embeddings import EmbeddingModelIdentity

    identity = EmbeddingModelIdentity(
        logical_name="full-reranker",
        model_id="BAAI/bge-reranker-v2-m3",
        revision="953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e",
        dimension=None,
        max_input_tokens=None,
        normalized=False,
        license="Apache-2.0",
    )

    class _FakeModel:
        def predict(self, pairs: list[tuple[str, str]], **_kwargs: object) -> list[float]:
            # Prefer the second pair.
            return [0.1, 0.9]

    reranker = CrossEncoderReranker(identity, enabled=True)

    async def _fake_ensure() -> _FakeModel:
        return _FakeModel()

    monkeypatch.setattr(reranker, "_ensure_model", _fake_ensure)

    hits = [
        _hit("a", "alpha", rrf=0.5, rank=1),
        _hit("b", "beta", rrf=0.4, rank=2),
    ]
    out = await reranker.rerank("q", hits, top_k=2)
    assert [hit.chunk.id for hit in out] == ["b", "a"]
    assert out[0].rrf_score == pytest.approx(0.4)
    assert out[1].rrf_score == pytest.approx(0.5)
    assert out[0].rerank_score == pytest.approx(0.9)
    assert out[0].matched_by is MatchedBy.RERANKED
    assert out[0].final_rank == 1
