"""FULL PostgreSQL hybrid retriever (FTS + exact vectors + RRF).

Vector backends:
- postgres_numpy_exact (portable Docker-free default; BYTEA + NumPy cosine)
- pgvector (optional when extension already installed)
"""

from __future__ import annotations

from collections.abc import Sequence

from halyk_agent.adapters.retrieval.errors import (
    EmbeddingDimensionError,
    HybridUnavailableError,
)
from halyk_agent.adapters.retrieval.postgres.backend import VectorBackendName
from halyk_agent.adapters.retrieval.postgres.deps import ensure_postgres_available
from halyk_agent.adapters.retrieval.postgres.filters import build_filter_sql
from halyk_agent.adapters.retrieval.postgres.models import DEFAULT_INDEX_KEY
from halyk_agent.adapters.retrieval.rrf import reciprocal_rank_fusion
from halyk_agent.domain.chunking import RetrievalChunk
from halyk_agent.domain.embeddings import EmbeddingModelIdentity
from halyk_agent.domain.retrieval import (
    IndexIdentity,
    MatchedBy,
    RetrievalHit,
    RetrievalQuery,
    RetrievalResult,
)

ensure_postgres_available()

from halyk_agent.adapters.retrieval.postgres.lexical import lexical_search  # noqa: E402
from halyk_agent.adapters.retrieval.postgres.repository import (  # noqa: E402
    PostgresRetrievalRepository,
)


class PostgresHybridRetriever:
    """Build and query a PostgreSQL hybrid (lexical + vector) index."""

    def __init__(
        self,
        dsn: str | None = None,
        *,
        index_key: str = DEFAULT_INDEX_KEY,
        repository: PostgresRetrievalRepository | None = None,
    ) -> None:
        ensure_postgres_available()
        self._repository: PostgresRetrievalRepository | None = repository
        if repository is None and dsn is not None:
            self._repository = PostgresRetrievalRepository(dsn, index_key=index_key)

    @property
    def dsn(self) -> str | None:
        return None if self._repository is None else self._repository.dsn

    async def build_index(
        self,
        chunks: Sequence[RetrievalChunk],
        embeddings: dict[str, list[float]],
        *,
        model_identity: EmbeddingModelIdentity,
        index_identity: IndexIdentity,
        dsn: str,
        index_key: str = DEFAULT_INDEX_KEY,
    ) -> None:
        """Create or replace the PostgreSQL index at ``dsn`` (transactional)."""
        if index_identity.embedding_model != model_identity:
            raise ValueError("index_identity.embedding_model must match model_identity")
        unknown = set(embeddings) - {chunk.id for chunk in chunks}
        if unknown:
            raise ValueError(f"embeddings reference unknown chunk ids: {sorted(unknown)[:5]}")
        repository = PostgresRetrievalRepository(dsn, index_key=index_key)
        await repository.ensure_schema()
        # Stamp vector backend into index identity for search-time routing / reports.
        backend = repository.vector_backend or VectorBackendName.POSTGRES_NUMPY_EXACT
        lex = dict(index_identity.lexical_configuration)
        lex["vector_backend"] = str(backend)
        lex.setdefault("backend", "postgres_simple")
        lex.setdefault("fts_config", "simple")
        lex.setdefault("policy", "or_lexemes")
        stamped = index_identity.model_copy(update={"lexical_configuration": lex})
        await repository.replace_index(
            chunks=chunks,
            embeddings=embeddings,
            model_identity=model_identity,
            index_identity=stamped,
        )
        if self._repository is not None and self._repository is not repository:
            await self._repository.dispose()
        self._repository = repository

    async def search(
        self,
        query: RetrievalQuery,
        *,
        query_embedding: list[float] | None,
        lexical_only: bool = False,
    ) -> RetrievalResult:
        """Search the open index; hybrid requires an explicit query embedding."""
        repository = self._require_repository()
        await repository.require_ready()
        index_identity = await repository.load_index_identity()
        embedding_model = await repository.load_embedding_model()
        backend = await repository.load_vector_backend()

        async with repository.session() as session:
            if lexical_only:
                lexical_hits, lexical_policy, _tokens = await lexical_search(
                    session,
                    query_text=query.text,
                    filters=query.filters,
                    limit=query.lexical_candidate_k,
                )
                hits = await self._hits_from_lexical(
                    repository,
                    lexical_hits,
                    top_k=query.top_k,
                )
                return RetrievalResult(
                    query=query,
                    hits=hits,
                    index_identity=index_identity,
                    embedding_model=embedding_model,
                    warnings=[
                        f"lexical_policy={lexical_policy}",
                        f"vector_backend={backend}",
                    ],
                )

            if query_embedding is None:
                raise HybridUnavailableError(
                    "hybrid search requires query_embedding; "
                    "pass lexical_only=True for explicit lexical-only retrieval"
                )
            if await repository.vector_count() < 1:
                raise HybridUnavailableError(
                    "hybrid search requires indexed embeddings; "
                    "index has no vectors and cannot silently downgrade to lexical-only"
                )
            expected_dim = embedding_model.dimension
            if expected_dim is not None and len(query_embedding) != expected_dim:
                raise EmbeddingDimensionError(
                    f"query embedding dimension {len(query_embedding)} "
                    f"does not match index dimension {expected_dim}"
                )

            lexical_hits, lexical_policy, _tokens = await lexical_search(
                session,
                query_text=query.text,
                filters=query.filters,
                limit=query.lexical_candidate_k,
            )
            vector_hits = await _vector_search_for_backend(
                backend,
                session,
                query_embedding=query_embedding,
                filters=query.filters,
                limit=query.vector_candidate_k,
                expected_dimension=expected_dim,
            )

        warnings = [
            f"lexical_policy={lexical_policy}",
            f"vector_backend={backend}",
        ]
        lexical_rank_map = {
            chunk_id: (rank, score) for rank, (chunk_id, score) in enumerate(lexical_hits, start=1)
        }
        vector_rank_map = {
            chunk_id: (rank, score) for rank, (chunk_id, score) in enumerate(vector_hits, start=1)
        }

        rrf_k = _rrf_k(index_identity)
        fused = reciprocal_rank_fusion(
            [
                [chunk_id for chunk_id, _ in lexical_hits],
                [chunk_id for chunk_id, _ in vector_hits],
            ],
            rrf_k=rrf_k,
        )

        selected = fused[: query.top_k]
        chunks = await repository.get_chunks([chunk_id for chunk_id, _, _ in selected])
        fused_hits: list[RetrievalHit] = []
        for final_rank, (chunk_id, rrf_score, _ranks) in enumerate(selected, start=1):
            chunk = chunks[chunk_id]
            lex = lexical_rank_map.get(chunk_id)
            vec = vector_rank_map.get(chunk_id)
            fused_hits.append(
                RetrievalHit(
                    chunk=chunk,
                    lexical_rank=None if lex is None else lex[0],
                    lexical_score=None if lex is None else lex[1],
                    vector_rank=None if vec is None else vec[0],
                    vector_score=None if vec is None else vec[1],
                    rrf_score=rrf_score,
                    final_rank=final_rank,
                    matched_by=MatchedBy.HYBRID,
                )
            )
        return RetrievalResult(
            query=query,
            hits=fused_hits,
            index_identity=index_identity,
            embedding_model=embedding_model,
            warnings=warnings,
        )

    def _require_repository(self) -> PostgresRetrievalRepository:
        if self._repository is None:
            raise HybridUnavailableError("no PostgreSQL index opened; call build_index first")
        return self._repository

    async def dispose(self) -> None:
        """Close the underlying engine when this retriever owns it."""
        if self._repository is not None:
            await self._repository.dispose()
            self._repository = None

    @staticmethod
    async def _hits_from_lexical(
        repository: PostgresRetrievalRepository,
        lexical_hits: list[tuple[str, float]],
        *,
        top_k: int,
    ) -> list[RetrievalHit]:
        selected = lexical_hits[:top_k]
        chunks = await repository.get_chunks([chunk_id for chunk_id, _ in selected])
        hits: list[RetrievalHit] = []
        for final_rank, (chunk_id, score) in enumerate(selected, start=1):
            hits.append(
                RetrievalHit(
                    chunk=chunks[chunk_id],
                    lexical_rank=final_rank,
                    lexical_score=score,
                    final_rank=final_rank,
                    matched_by=MatchedBy.LEXICAL,
                )
            )
        return hits


async def _vector_search_for_backend(
    backend: VectorBackendName,
    session: object,
    *,
    query_embedding: list[float],
    filters: object,
    limit: int,
    expected_dimension: int | None,
) -> list[tuple[str, float]]:
    if backend is VectorBackendName.PGVECTOR:
        from halyk_agent.adapters.retrieval.postgres.vectors import vector_search

        return await vector_search(
            session,  # type: ignore[arg-type]
            query_embedding=query_embedding,
            filters=filters,  # type: ignore[arg-type]
            limit=limit,
            expected_dimension=expected_dimension,
        )
    from halyk_agent.adapters.retrieval.postgres.numpy_vectors import numpy_exact_vector_search

    return await numpy_exact_vector_search(
        session,  # type: ignore[arg-type]
        query_embedding=query_embedding,
        filters=filters,  # type: ignore[arg-type]
        limit=limit,
        expected_dimension=expected_dimension,
    )


def _rrf_k(index_identity: IndexIdentity) -> int:
    raw = index_identity.rrf_configuration.get("rrf_k", 60)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError("rrf_configuration.rrf_k must be an int")
    if raw < 0:
        raise ValueError("rrf_configuration.rrf_k must be >= 0")
    return raw


__all__ = [
    "PostgresHybridRetriever",
    "build_filter_sql",
]
