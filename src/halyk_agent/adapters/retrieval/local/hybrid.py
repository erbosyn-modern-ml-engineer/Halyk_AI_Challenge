"""FAST local hybrid retriever (SQLite FTS5 + brute-force vectors + RRF)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from halyk_agent.adapters.retrieval.errors import (
    EmbeddingDimensionError,
    HybridUnavailableError,
)
from halyk_agent.adapters.retrieval.local.lexical import lexical_search
from halyk_agent.adapters.retrieval.local.sqlite_store import SqliteRetrievalStore
from halyk_agent.adapters.retrieval.local.vectors import brute_force_cosine_topk
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


class LocalHybridRetriever:
    """Build and query a local SQLite hybrid (lexical + vector) index."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._store: SqliteRetrievalStore | None = None
        if db_path is not None:
            self._store = SqliteRetrievalStore(db_path)
            self._store.require_ready()

    @property
    def db_path(self) -> Path | None:
        return None if self._store is None else self._store.db_path

    def build_index(
        self,
        chunks: Sequence[RetrievalChunk],
        embeddings: dict[str, list[float]],
        *,
        model_identity: EmbeddingModelIdentity,
        index_identity: IndexIdentity,
        db_path: str | Path,
    ) -> None:
        """Create or replace the local index at ``db_path``."""
        if index_identity.embedding_model != model_identity:
            raise ValueError("index_identity.embedding_model must match model_identity")
        unknown = set(embeddings) - {chunk.id for chunk in chunks}
        if unknown:
            raise ValueError(f"embeddings reference unknown chunk ids: {sorted(unknown)[:5]}")
        store = SqliteRetrievalStore(db_path)
        store.initialize_schema()
        store.replace_index(
            chunks=chunks,
            embeddings=embeddings,
            model_identity=model_identity,
            index_identity=index_identity,
        )
        self._store = store

    def search(
        self,
        query: RetrievalQuery,
        *,
        query_embedding: list[float] | None,
        lexical_only: bool = False,
    ) -> RetrievalResult:
        """Search the open index; hybrid requires an explicit query embedding."""
        store = self._require_store()
        store.require_ready()
        index_identity = store.load_index_identity()
        embedding_model = store.load_embedding_model()
        connection = store.connect()

        if lexical_only:
            lexical_hits = lexical_search(
                connection,
                query_text=query.text,
                filters=query.filters,
                limit=query.lexical_candidate_k,
            )
            hits = self._hits_from_lexical(store, lexical_hits, top_k=query.top_k)
            return RetrievalResult(
                query=query,
                hits=hits,
                index_identity=index_identity,
                embedding_model=embedding_model,
                warnings=[],
            )

        if query_embedding is None:
            raise HybridUnavailableError(
                "hybrid search requires query_embedding; "
                "pass lexical_only=True for explicit lexical-only retrieval"
            )
        if store.vector_count() < 1:
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

        lexical_hits = lexical_search(
            connection,
            query_text=query.text,
            filters=query.filters,
            limit=query.lexical_candidate_k,
        )
        filtered_vectors = store.iter_filtered_embeddings(query.filters)
        vector_hits = brute_force_cosine_topk(
            query_embedding,
            filtered_vectors,
            top_k=query.vector_candidate_k,
        )

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

        chunk_ids = [chunk_id for chunk_id, _, _ in fused[: query.top_k]]
        chunks = store.get_chunks(chunk_ids)
        fused_hits: list[RetrievalHit] = []
        for final_rank, (chunk_id, rrf_score, _ranks) in enumerate(
            fused[: query.top_k],
            start=1,
        ):
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
            warnings=[],
        )

    def _require_store(self) -> SqliteRetrievalStore:
        if self._store is None:
            raise HybridUnavailableError("no local index opened; call build_index first")
        return self._store

    @staticmethod
    def _hits_from_lexical(
        store: SqliteRetrievalStore,
        lexical_hits: list[tuple[str, float]],
        *,
        top_k: int,
    ) -> list[RetrievalHit]:
        selected = lexical_hits[:top_k]
        chunks = store.get_chunks([chunk_id for chunk_id, _ in selected])
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


def _rrf_k(index_identity: IndexIdentity) -> int:
    raw = index_identity.rrf_configuration.get("rrf_k", 60)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError("rrf_configuration.rrf_k must be an int")
    if raw < 0:
        raise ValueError("rrf_configuration.rrf_k must be >= 0")
    return raw


__all__ = ["LocalHybridRetriever"]
