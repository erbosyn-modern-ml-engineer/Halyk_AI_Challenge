"""PostgreSQL hybrid retrieval tests (skip when DSN / docker unavailable)."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest

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

pytestmark = pytest.mark.postgres


def _dsn() -> str | None:
    return os.environ.get("HALYK_POSTGRES_DSN")


async def _postgres_reachable(dsn: str) -> bool:
    try:
        from halyk_agent.adapters.retrieval.postgres.deps import ensure_postgres_available

        ensure_postgres_available()
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(dsn, pool_pre_ping=True)
        try:
            async with engine.connect() as connection:
                await connection.execute(
                    __import__("sqlalchemy", fromlist=["text"]).text("SELECT 1")
                )
            return True
        finally:
            await engine.dispose()
    except Exception:
        return False


@pytest.fixture
async def postgres_dsn() -> AsyncIterator[str]:
    dsn = _dsn()
    if not dsn:
        pytest.fail("HALYK_POSTGRES_DSN not set — cannot verify live PostgreSQL")
    if not await _postgres_reachable(dsn):
        pytest.fail("PostgreSQL unreachable via HALYK_POSTGRES_DSN")
    os.environ["HALYK_VECTOR_BACKEND"] = "postgres_numpy_exact"
    yield dsn


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
        query_prefix="",
        passage_prefix="",
        license="MIT",
    )


def _index_identity(model: EmbeddingModelIdentity) -> IndexIdentity:
    return IndexIdentity(
        profile="full",
        chunk_manifest_hash="a" * 64,
        chunker_identity=_chunker(),
        embedding_model=model,
        lexical_configuration={"fts_config": "simple"},
        rrf_configuration={"rrf_k": 60},
    )


def _make_chunk(
    *,
    chunk_id: str,
    document_id: str,
    text: str,
    page_numbers: list[int] | None = None,
    ordinal: int = 0,
) -> RetrievalChunk:
    return RetrievalChunk(
        id=chunk_id,
        document_id=document_id,
        document_version_id=f"{document_id}-v1",
        artifact_id=f"art-{document_id}",
        source_file="doc.pdf",
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


@pytest.mark.asyncio
async def test_postgres_fts_and_filters(postgres_dsn: str) -> None:
    from halyk_agent.adapters.retrieval.postgres import PostgresHybridRetriever

    model = _model()
    chunks = [
        _make_chunk(
            chunk_id="c-ru",
            document_id="doc-keep",
            text="лимит по договору CONTRACT-42",
            ordinal=0,
        ),
        _make_chunk(
            chunk_id="c-drop",
            document_id="doc-drop",
            text="лимит по договору CONTRACT-42",
            ordinal=1,
        ),
    ]
    embeddings = {"c-ru": _unit(0), "c-drop": _unit(1)}
    retriever = PostgresHybridRetriever()
    await retriever.build_index(
        chunks,
        embeddings,
        model_identity=model,
        index_identity=_index_identity(model),
        dsn=postgres_dsn,
        index_key="test-fts",
    )
    try:
        result = await retriever.search(
            RetrievalQuery(
                text="лимит CONTRACT-42",
                filters=RetrievalFilters(document_ids=["doc-keep"]),
                top_k=2,
                lexical_candidate_k=2,
            ),
            query_embedding=None,
            lexical_only=True,
        )
        assert [hit.chunk.id for hit in result.hits] == ["c-ru"]
        assert result.hits[0].matched_by is MatchedBy.LEXICAL
    finally:
        await retriever.dispose()


@pytest.mark.asyncio
async def test_postgres_hybrid_rrf_and_failed_build_not_ready(postgres_dsn: str) -> None:
    from halyk_agent.adapters.retrieval.errors import (
        HybridUnavailableError,
        IndexNotReadyError,
    )
    from halyk_agent.adapters.retrieval.postgres import PostgresHybridRetriever
    from halyk_agent.adapters.retrieval.postgres.repository import (
        PostgresRetrievalRepository,
    )

    model = _model()
    chunk = _make_chunk(chunk_id="only", document_id="d1", text="договор payment limit")
    retriever = PostgresHybridRetriever()
    await retriever.build_index(
        [chunk],
        {"only": [1.0, 0.0, 0.0, 0.0]},
        model_identity=model,
        index_identity=_index_identity(model),
        dsn=postgres_dsn,
        index_key="test-hybrid",
    )
    try:
        query = RetrievalQuery(text="договор", top_k=1, lexical_candidate_k=1)
        with pytest.raises(HybridUnavailableError):
            await retriever.search(query, query_embedding=None, lexical_only=False)

        hybrid = await retriever.search(
            query,
            query_embedding=[1.0, 0.0, 0.0, 0.0],
            lexical_only=False,
        )
        assert hybrid.hits[0].matched_by is MatchedBy.HYBRID
        assert hybrid.hits[0].rrf_score is not None

        # Failed rebuild must not mark ready (transaction rolls back).
        repo = PostgresRetrievalRepository(postgres_dsn, index_key="test-fail")
        await repo.ensure_schema()
        with pytest.raises(ValueError, match="unknown chunk"):
            await repo.replace_index(
                chunks=[chunk],
                embeddings={"missing": [1.0, 0.0, 0.0, 0.0]},
                model_identity=model,
                index_identity=_index_identity(model),
            )
        with pytest.raises(IndexNotReadyError):
            await repo.require_ready()
        await repo.dispose()
    finally:
        await retriever.dispose()
