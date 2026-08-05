"""Live PostgreSQL verification for postgres_numpy_exact (Stage 4.3)."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from halyk_agent.adapters.retrieval.local.vectors import pack_float32_vector
from halyk_agent.adapters.retrieval.postgres.hybrid import PostgresHybridRetriever
from halyk_agent.adapters.retrieval.postgres.lexical import (
    LEXICAL_POLICY_AND,
    LEXICAL_POLICY_OR,
    lexical_search,
)
from halyk_agent.adapters.retrieval.postgres.models import RetrievalEmbeddingRow
from halyk_agent.adapters.retrieval.postgres.numpy_vectors import numpy_exact_vector_search
from halyk_agent.adapters.retrieval.postgres.repository import PostgresRetrievalRepository
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
    RetrievalFilters,
    RetrievalQuery,
)

pytestmark = pytest.mark.postgres


def _dsn() -> str | None:
    return os.environ.get("HALYK_POSTGRES_DSN")


async def _reachable(dsn: str) -> bool:
    try:
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(dsn, pool_pre_ping=True)
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
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
    if not await _reachable(dsn):
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


def _model(*, dimension: int = 4, revision: str = "rev1") -> EmbeddingModelIdentity:
    return EmbeddingModelIdentity(
        logical_name="test-embed",
        model_id="test/embed",
        revision=revision,
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
        lexical_configuration={
            "fts_config": "simple",
            "policy": "or_lexemes",
            "vector_backend": "postgres_numpy_exact",
        },
        rrf_configuration={"rrf_k": 60},
    )


def _make_chunk(
    *,
    chunk_id: str,
    document_id: str,
    text: str,
    page_numbers: list[int] | None = None,
    kind: ChunkKind = ChunkKind.TEXT,
    level: ChunkLevel = ChunkLevel.ATOMIC,
    source_file: str = "doc.pdf",
    artifact_id: str = "art-1",
    document_version_id: str = "ver-1",
) -> RetrievalChunk:
    return RetrievalChunk(
        id=chunk_id,
        document_id=document_id,
        document_version_id=document_version_id,
        artifact_id=artifact_id,
        source_file=source_file,
        kind=kind,
        level=level,
        page_numbers=page_numbers or [1],
        raw_text=text,
        retrieval_text=text,
        retrieval_text_kind=RetrievalTextKind.RAW_SOURCE,
        parent_chunk_id=None,
        source_block_ids=["b1"],
        source_table_ids=[],
        evidence_span_ids=["e1"],
        heading_path=[],
        ordinal=0,
        character_count=len(text),
        estimated_token_count=max(1, len(text) // 4),
        metadata={},
    )


@pytest.mark.asyncio
async def test_schema_numpy_tables_without_requiring_pgvector(postgres_dsn: str) -> None:
    repo = PostgresRetrievalRepository(
        postgres_dsn,
        index_key="schema-check",
        vector_backend="postgres_numpy_exact",
    )
    try:
        await repo.ensure_schema()
        async with repo.session() as session:
            ext = (
                await session.execute(
                    text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
                )
            ).scalar()
            assert ext is None  # must not auto-install
            for table in (
                "retrieval_chunks",
                "retrieval_embeddings",
                "retrieval_indexes",
            ):
                exists = (
                    await session.execute(
                        text("SELECT to_regclass(:name)"),
                        {"name": table},
                    )
                ).scalar_one()
                assert exists is not None
            blob_col = (
                await session.execute(
                    text(
                        """
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'retrieval_embeddings'
                          AND column_name = 'embedding_blob'
                        """
                    )
                )
            ).scalar()
            assert blob_col == 1
    finally:
        await repo.dispose()


@pytest.mark.asyncio
async def test_replace_index_idempotent_and_ready(postgres_dsn: str) -> None:
    model = _model()
    identity = _index_identity(model)
    chunk = _make_chunk(chunk_id="c-idem", document_id="d1", text="лимит клиента")
    embeddings = {"c-idem": [1.0, 0.0, 0.0, 0.0]}
    retriever = PostgresHybridRetriever(postgres_dsn, index_key="idem")
    try:
        await retriever.build_index(
            [chunk],
            embeddings,
            model_identity=model,
            index_identity=identity,
            dsn=postgres_dsn,
            index_key="idem",
        )
        await retriever.build_index(
            [chunk],
            embeddings,
            model_identity=model,
            index_identity=identity,
            dsn=postgres_dsn,
            index_key="idem",
        )
        repo = retriever._require_repository()
        assert await repo.vector_count() == 1
        await repo.require_ready()
        backend = await repo.load_vector_backend()
        assert str(backend) == "postgres_numpy_exact"
    finally:
        await retriever.dispose()


@pytest.mark.asyncio
async def test_embedding_uniqueness_includes_revision(postgres_dsn: str) -> None:
    repo = PostgresRetrievalRepository(
        postgres_dsn,
        index_key="uniq",
        vector_backend="postgres_numpy_exact",
    )
    model = _model(revision="rev-a")
    identity = _index_identity(model)
    chunk = _make_chunk(chunk_id="c-uniq", document_id="d1", text="unique embedding row")
    try:
        await repo.ensure_schema()
        await repo.replace_index(
            chunks=[chunk],
            embeddings={"c-uniq": [0.0, 1.0, 0.0, 0.0]},
            model_identity=model,
            index_identity=identity,
        )
        blob, dimension, checksum = pack_float32_vector([0.0, 0.0, 1.0, 0.0])
        async with repo.session() as session, session.begin():
            session.add(
                RetrievalEmbeddingRow(
                    chunk_id="c-uniq",
                    model_id=model.model_id,
                    model_revision=model.revision,
                    dimension=dimension,
                    embedding_blob=blob,
                    vector_checksum=checksum,
                )
            )
            with pytest.raises(IntegrityError):
                await session.flush()
            await session.rollback()
    finally:
        await repo.dispose()


@pytest.mark.asyncio
async def test_failed_build_rolls_back_and_keeps_prior_ready(postgres_dsn: str) -> None:
    repo = PostgresRetrievalRepository(
        postgres_dsn,
        index_key="fail-ready",
        vector_backend="postgres_numpy_exact",
    )
    model = _model()
    identity = _index_identity(model)
    good = _make_chunk(chunk_id="c-ok", document_id="d1", text="ok chunk text here")
    try:
        await repo.ensure_schema()
        await repo.replace_index(
            chunks=[good],
            embeddings={"c-ok": [1.0, 0.0, 0.0, 0.0]},
            model_identity=model,
            index_identity=identity,
        )
        await repo.require_ready()
        assert await repo.vector_count() == 1

        bad = _make_chunk(chunk_id="c-bad", document_id="d1", text="bad chunk text here")
        with pytest.raises(ValueError, match="dimension"):
            await repo.replace_index(
                chunks=[good, bad],
                embeddings={
                    "c-ok": [1.0, 0.0, 0.0, 0.0],
                    "c-bad": [1.0, 0.0],
                },
                model_identity=model,
                index_identity=identity,
            )
        # Prior successful index restored by rollback.
        await repo.require_ready()
        assert await repo.vector_count() == 1
        chunks = await repo.get_chunks(["c-ok"])
        assert "c-ok" in chunks
    finally:
        await repo.dispose()


@pytest.mark.asyncio
async def test_fts_or_partial_multitoken(postgres_dsn: str) -> None:
    model = _model()
    identity = _index_identity(model)
    limit_chunk = _make_chunk(
        chunk_id="c-limit",
        document_id="d-limit",
        text="Клиентский лимит операций составляет миллион.",
    )
    dogovor_chunk = _make_chunk(
        chunk_id="c-dogovor",
        document_id="d-dogovor",
        text="Условия по договору без упоминания лимита.",
    )
    embeddings = {
        "c-limit": [1.0, 0.0, 0.0, 0.0],
        "c-dogovor": [0.0, 1.0, 0.0, 0.0],
    }
    retriever = PostgresHybridRetriever(postgres_dsn, index_key="fts-or")
    try:
        await retriever.build_index(
            [limit_chunk, dogovor_chunk],
            embeddings,
            model_identity=model,
            index_identity=identity,
            dsn=postgres_dsn,
            index_key="fts-or",
        )
        repo = retriever._require_repository()
        async with repo.session() as session:
            or_hits, policy, _tokens = await lexical_search(
                session,
                query_text="лимит по договору",
                filters=RetrievalFilters(),
                limit=10,
                lexical_policy=LEXICAL_POLICY_OR,
            )
            assert policy == LEXICAL_POLICY_OR
            or_ids = {chunk_id for chunk_id, _ in or_hits}
            assert "c-limit" in or_ids
            assert "c-dogovor" in or_ids

            and_hits, and_policy, _ = await lexical_search(
                session,
                query_text="лимит по договору",
                filters=RetrievalFilters(),
                limit=10,
                lexical_policy=LEXICAL_POLICY_AND,
            )
            assert and_policy == LEXICAL_POLICY_AND
            and_ids = {chunk_id for chunk_id, _ in and_hits}
            assert and_ids == set()
    finally:
        await retriever.dispose()


@pytest.mark.asyncio
async def test_numpy_filters_before_scoring_and_cosine_order(postgres_dsn: str) -> None:
    model = _model()
    identity = _index_identity(model)
    included = _make_chunk(
        chunk_id="c-in",
        document_id="doc-keep",
        text="ordinary limit text",
    )
    excluded = _make_chunk(
        chunk_id="c-out",
        document_id="doc-drop",
        text="ordinary limit text boosted",
    )
    near = _make_chunk(
        chunk_id="c-near",
        document_id="doc-keep",
        text="near neighbor limit text",
    )
    embeddings = {
        "c-in": [0.2, 0.8, 0.0, 0.0],
        "c-out": [1.0, 0.0, 0.0, 0.0],
        "c-near": [0.9, 0.1, 0.0, 0.0],
    }
    query_embedding = [1.0, 0.0, 0.0, 0.0]
    retriever = PostgresHybridRetriever(postgres_dsn, index_key="filt")
    try:
        await retriever.build_index(
            [included, excluded, near],
            embeddings,
            model_identity=model,
            index_identity=identity,
            dsn=postgres_dsn,
            index_key="filt",
        )
        repo = retriever._require_repository()
        filters = RetrievalFilters(document_ids=["doc-keep"])
        async with repo.session() as session:
            vec = await numpy_exact_vector_search(
                session,
                query_embedding=query_embedding,
                filters=filters,
                limit=10,
                expected_dimension=4,
            )
            lex, _, _ = await lexical_search(
                session,
                query_text="limit",
                filters=filters,
                limit=10,
            )
        assert all(chunk_id != "c-out" for chunk_id, _ in vec)
        assert all(chunk_id != "c-out" for chunk_id, _ in lex)
        assert [chunk_id for chunk_id, _ in vec] == ["c-near", "c-in"]

        result = await retriever.search(
            RetrievalQuery(
                text="limit",
                filters=filters,
                top_k=5,
            ),
            query_embedding=query_embedding,
        )
        assert all(hit.chunk.document_id == "doc-keep" for hit in result.hits)
        assert any(w.startswith("lexical_policy=") for w in result.warnings)
        assert any("postgres_numpy_exact" in w for w in result.warnings)
    finally:
        await retriever.dispose()


@pytest.mark.asyncio
async def test_multilingual_kazakh_russian_identifiers(postgres_dsn: str) -> None:
    model = _model()
    identity = _index_identity(model)
    kk = _make_chunk(
        chunk_id="c-kk",
        document_id="d-kk",
        text="Клиент лимиті CTR-2024-99 бойынша бекітілді.",
    )
    ru = _make_chunk(
        chunk_id="c-ru",
        document_id="d-ru",
        text="Лимит по договору CTR-2024-99 утверждён.",
    )
    embeddings = {
        "c-kk": [1.0, 0.0, 0.0, 0.0],
        "c-ru": [0.0, 1.0, 0.0, 0.0],
    }
    retriever = PostgresHybridRetriever(postgres_dsn, index_key="multi")
    try:
        await retriever.build_index(
            [kk, ru],
            embeddings,
            model_identity=model,
            index_identity=identity,
            dsn=postgres_dsn,
            index_key="multi",
        )
        repo = retriever._require_repository()
        async with repo.session() as session:
            hits, _, tokens = await lexical_search(
                session,
                query_text="CTR-2024-99 лимит",
                filters=RetrievalFilters(),
                limit=10,
            )
        assert "ctr-2024-99" in tokens
        ids = {chunk_id for chunk_id, _ in hits}
        assert "c-kk" in ids
        assert "c-ru" in ids
    finally:
        await retriever.dispose()
