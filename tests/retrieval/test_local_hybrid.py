"""Local hybrid retrieval acceptance tests (FAST SQLite path)."""

from __future__ import annotations

import hashlib
import sqlite3
import struct
from pathlib import Path

import pytest

from halyk_agent.adapters.retrieval.errors import (
    CorruptEmbeddingBlobError,
    HybridUnavailableError,
)
from halyk_agent.adapters.retrieval.local import LocalHybridRetriever
from halyk_agent.adapters.retrieval.local.sqlite_store import SqliteRetrievalStore
from halyk_agent.adapters.retrieval.local.vectors import (
    pack_float32_vector,
    unpack_float32_vector,
)
from halyk_agent.adapters.retrieval.rrf import reciprocal_rank_fusion
from halyk_agent.domain.chunking import (
    ChunkerIdentity,
    ChunkKind,
    ChunkLevel,
    RetrievalChunk,
    RetrievalTextKind,
)
from halyk_agent.domain.embeddings import EmbeddingModelIdentity
from halyk_agent.domain.retrieval import (
    IndexIdentity,
    MatchedBy,
    RetrievalFilters,
    RetrievalQuery,
)


def _chunker() -> ChunkerIdentity:
    return ChunkerIdentity(
        name="test-chunker",
        version="1",
        configuration_hash="cfg",
        normalization_version="norm-1",
    )


def _model(*, dimension: int = 4) -> EmbeddingModelIdentity:
    return EmbeddingModelIdentity(
        logical_name="test-embed",
        model_id="test/embed",
        revision="rev1",
        dimension=dimension,
        max_input_tokens=512,
        normalized=True,
        query_prefix="query: ",
        passage_prefix="passage: ",
        license="MIT",
    )


def _index_identity(model: EmbeddingModelIdentity) -> IndexIdentity:
    return IndexIdentity(
        profile="fast",
        chunk_manifest_hash="a" * 64,
        chunker_identity=_chunker(),
        embedding_model=model,
        lexical_configuration={"tokenizer": "unicode61"},
        rrf_configuration={"rrf_k": 60},
    )


def _make_chunk(
    *,
    chunk_id: str,
    document_id: str,
    text: str,
    page_numbers: list[int] | None = None,
    ordinal: int = 0,
    source_file: str = "doc.pdf",
) -> RetrievalChunk:
    return RetrievalChunk(
        id=chunk_id,
        document_id=document_id,
        document_version_id=f"{document_id}-v1",
        artifact_id=f"art-{document_id}",
        source_file=source_file,
        kind=ChunkKind.TEXT,
        level=ChunkLevel.ATOMIC,
        page_numbers=page_numbers or [1],
        raw_text=text,
        retrieval_text=text,
        retrieval_text_kind=RetrievalTextKind.RAW_SOURCE,
        evidence_span_ids=[f"span-{chunk_id}"],
        ordinal=ordinal,
        character_count=len(text),
        estimated_token_count=max(1, len(text) // 4),
    )


def _unit(i: int, dim: int = 4) -> list[float]:
    vector = [0.0] * dim
    vector[i % dim] = 1.0
    return vector


def test_fts_mixed_cyrillic_latin(tmp_path: Path) -> None:
    model = _model()
    chunks = [
        _make_chunk(
            chunk_id="c-ru",
            document_id="doc-a",
            text="лимит по договору CONTRACT-42",
            ordinal=0,
        ),
        _make_chunk(
            chunk_id="c-en",
            document_id="doc-b",
            text="unrelated English invoice terms",
            ordinal=1,
        ),
    ]
    embeddings = {
        "c-ru": _unit(0),
        "c-en": _unit(1),
    }
    retriever = LocalHybridRetriever()
    retriever.build_index(
        chunks,
        embeddings,
        model_identity=model,
        index_identity=_index_identity(model),
        db_path=tmp_path / "index.sqlite",
    )
    result = retriever.search(
        RetrievalQuery(text="лимит CONTRACT-42", top_k=2, lexical_candidate_k=2),
        query_embedding=None,
        lexical_only=True,
    )
    assert result.hits
    assert result.hits[0].chunk.id == "c-ru"
    assert result.hits[0].matched_by is MatchedBy.LEXICAL
    assert "лимит" in result.hits[0].chunk.retrieval_text
    assert "CONTRACT-42" in result.hits[0].chunk.retrieval_text


def test_filters_applied_before_scoring(tmp_path: Path) -> None:
    model = _model()
    # Same strong lexical/vector signal in both docs; filter must exclude doc-drop.
    chunks = [
        _make_chunk(
            chunk_id="keep",
            document_id="doc-keep",
            text="payment limit payment limit",
            page_numbers=[1],
            ordinal=0,
        ),
        _make_chunk(
            chunk_id="drop",
            document_id="doc-drop",
            text="payment limit payment limit",
            page_numbers=[2],
            ordinal=1,
        ),
    ]
    embeddings = {
        "keep": [1.0, 0.0, 0.0, 0.0],
        "drop": [1.0, 0.0, 0.0, 0.0],
    }
    retriever = LocalHybridRetriever()
    retriever.build_index(
        chunks,
        embeddings,
        model_identity=model,
        index_identity=_index_identity(model),
        db_path=tmp_path / "index.sqlite",
    )
    query = RetrievalQuery(
        text="payment limit",
        filters=RetrievalFilters(document_ids=["doc-keep"]),
        top_k=5,
        lexical_candidate_k=5,
        vector_candidate_k=5,
    )
    hybrid = retriever.search(query, query_embedding=[1.0, 0.0, 0.0, 0.0])
    assert [hit.chunk.id for hit in hybrid.hits] == ["keep"]
    assert all(hit.chunk.document_id == "doc-keep" for hit in hybrid.hits)

    lexical = retriever.search(query, query_embedding=None, lexical_only=True)
    assert [hit.chunk.id for hit in lexical.hits] == ["keep"]


def test_rrf_formula_dedupe_and_tie_break() -> None:
    fused = reciprocal_rank_fusion([["a", "b"], ["b", "a"]], rrf_k=60)
    score_a = 1.0 / (60 + 1) + 1.0 / (60 + 2)
    score_b = 1.0 / (60 + 2) + 1.0 / (60 + 1)
    assert score_a == pytest.approx(score_b)
    by_id = {chunk_id: (score, ranks) for chunk_id, score, ranks in fused}
    assert by_id["a"][0] == pytest.approx(score_a)
    assert by_id["b"][0] == pytest.approx(score_b)
    # Equal scores: deterministic tie-break by chunk_id ascending.
    assert [chunk_id for chunk_id, _, _ in fused] == ["a", "b"]
    assert by_id["a"][1] == {"0": 1, "1": 2}
    assert by_id["b"][1] == {"0": 2, "1": 1}

    # Duplicate IDs within a list are ignored after the first occurrence;
    # later unique IDs keep their original 1-based positions.
    deduped = reciprocal_rank_fusion([["x", "x", "y"]], rrf_k=60)
    assert [chunk_id for chunk_id, _, _ in deduped] == ["x", "y"]
    assert deduped[0][1] == pytest.approx(1.0 / (60 + 1))
    assert deduped[1][1] == pytest.approx(1.0 / (60 + 3))
    assert deduped[1][2] == {"0": 3}


def test_corrupt_blob_rejected(tmp_path: Path) -> None:
    blob, dimension, checksum = pack_float32_vector([0.1, 0.2, 0.3, 0.4])
    assert len(blob) == 16
    corrupt = bytearray(blob)
    corrupt[0] ^= 0xFF
    with pytest.raises(CorruptEmbeddingBlobError):
        unpack_float32_vector(bytes(corrupt), dimension=dimension, checksum=checksum)

    truncated = blob[:-1]
    with pytest.raises(CorruptEmbeddingBlobError):
        unpack_float32_vector(truncated, dimension=dimension, checksum=checksum)

    # Stored row with mutated BLOB but stale checksum must fail at read time.
    model = _model()
    chunk = _make_chunk(chunk_id="c1", document_id="d1", text="alpha beta")
    db_path = tmp_path / "corrupt.sqlite"
    retriever = LocalHybridRetriever()
    retriever.build_index(
        [chunk],
        {"c1": [1.0, 0.0, 0.0, 0.0]},
        model_identity=model,
        index_identity=_index_identity(model),
        db_path=db_path,
    )
    connection = sqlite3.connect(str(db_path))
    bad_blob = struct.pack("<4f", 0.0, 1.0, 0.0, 0.0)
    connection.execute(
        "UPDATE embedding_records SET vector_blob = ? WHERE chunk_id = ?",
        (bad_blob, "c1"),
    )
    connection.commit()
    connection.close()

    store = SqliteRetrievalStore(db_path)
    with pytest.raises(CorruptEmbeddingBlobError):
        store.iter_filtered_embeddings(RetrievalFilters())


def test_hybrid_cannot_silent_downgrade(tmp_path: Path) -> None:
    model = _model()
    chunk = _make_chunk(chunk_id="only-lex", document_id="d1", text="договор limit")
    retriever = LocalHybridRetriever()
    # Lexical index without embeddings.
    retriever.build_index(
        [chunk],
        {},
        model_identity=model,
        index_identity=_index_identity(model),
        db_path=tmp_path / "lex-only.sqlite",
    )
    query = RetrievalQuery(text="договор", top_k=1, lexical_candidate_k=1)

    with pytest.raises(HybridUnavailableError):
        retriever.search(query, query_embedding=None, lexical_only=False)

    with pytest.raises(HybridUnavailableError):
        retriever.search(query, query_embedding=[1.0, 0.0, 0.0, 0.0], lexical_only=False)

    explicit = retriever.search(query, query_embedding=None, lexical_only=True)
    assert explicit.hits[0].matched_by is MatchedBy.LEXICAL

    # With embeddings present, omitting query_embedding still must not downgrade.
    retriever.build_index(
        [chunk],
        {"only-lex": [1.0, 0.0, 0.0, 0.0]},
        model_identity=model,
        index_identity=_index_identity(model),
        db_path=tmp_path / "hybrid.sqlite",
    )
    with pytest.raises(HybridUnavailableError):
        retriever.search(query, query_embedding=None, lexical_only=False)

    hybrid = retriever.search(
        query,
        query_embedding=[1.0, 0.0, 0.0, 0.0],
        lexical_only=False,
    )
    assert hybrid.hits[0].matched_by is MatchedBy.HYBRID
    assert hybrid.hits[0].rrf_score is not None


def test_vector_checksum_is_sha256_of_blob() -> None:
    values = [0.25, -0.5, 0.0, 1.0]
    blob, dimension, checksum = pack_float32_vector(values)
    assert dimension == 4
    assert checksum == hashlib.sha256(blob).hexdigest()
    restored = unpack_float32_vector(blob, dimension=dimension, checksum=checksum)
    assert restored == pytest.approx(values)
