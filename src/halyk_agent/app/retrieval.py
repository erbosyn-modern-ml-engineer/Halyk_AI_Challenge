"""Retrieval search application service."""

from __future__ import annotations

import json
import os
from pathlib import Path

from halyk_agent.adapters.embeddings.model_registry import (
    FAST_EMBEDDING_LOGICAL_NAME,
    FULL_EMBEDDING_LOGICAL_NAME,
    FULL_RERANKER_LOGICAL_NAME,
)
from halyk_agent.adapters.embeddings.sentence_transformer_provider import (
    SentenceTransformerEmbeddingProvider,
)
from halyk_agent.adapters.retrieval.local.hybrid import LocalHybridRetriever
from halyk_agent.domain.chunking import ChunkKind
from halyk_agent.domain.retrieval import (
    RetrievalFilters,
    RetrievalQuery,
    RetrievalResult,
)


class RetrievalServiceError(Exception):
    """Typed retrieval failure."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


async def search_index(
    *,
    index_dir: Path | None,
    query_text: str,
    profile: str,
    top_k: int = 10,
    document_ids: list[str] | None = None,
    document_version_ids: list[str] | None = None,
    source_files: list[str] | None = None,
    page_numbers: list[int] | None = None,
    chunk_kinds: list[str] | None = None,
    include_parent_context: bool = False,
    rerank: bool = False,
    lexical_only: bool = False,
) -> RetrievalResult:
    """Run hybrid (or lexical-only) search against a FAST or FULL index."""
    profile_norm = profile.lower().strip()
    if profile_norm not in {"fast", "full"}:
        raise RetrievalServiceError("profile must be fast or full")

    filters = RetrievalFilters(
        document_ids=document_ids or [],
        document_version_ids=document_version_ids or [],
        source_files=source_files or [],
        page_numbers=page_numbers or [],
        chunk_kinds=[ChunkKind(k) for k in (chunk_kinds or [])],
        chunk_levels=[],
    )
    query = RetrievalQuery(
        text=query_text,
        filters=filters,
        top_k=top_k,
        include_parent_context=include_parent_context,
    )

    query_embedding = None
    if not lexical_only:
        logical = (
            FAST_EMBEDDING_LOGICAL_NAME if profile_norm == "fast" else FULL_EMBEDDING_LOGICAL_NAME
        )
        provider = SentenceTransformerEmbeddingProvider.from_logical_name(logical)
        embedded = await provider.embed_query(query.text)
        query_embedding = list(embedded.values)

    if profile_norm == "fast":
        if index_dir is None:
            raise RetrievalServiceError("--index is required for FAST search")
        db_path = index_dir / "local_index.sqlite"
        if not db_path.is_file():
            raise RetrievalServiceError("local_index.sqlite not found in index directory")
        result = LocalHybridRetriever(db_path).search(
            query,
            query_embedding=query_embedding,
            lexical_only=lexical_only,
        )
    else:
        from halyk_agent.adapters.retrieval.postgres.hybrid import (
            PostgresHybridRetriever,
        )

        dsn = os.environ.get("HALYK_POSTGRES_DSN")
        if not dsn:
            raise RetrievalServiceError("HALYK_POSTGRES_DSN is required for FULL search")
        result = await PostgresHybridRetriever(dsn).search(
            query,
            query_embedding=query_embedding,
            lexical_only=lexical_only,
        )

    if rerank and result.hits:
        from halyk_agent.adapters.reranking.cross_encoder import CrossEncoderReranker

        reranker = CrossEncoderReranker.from_logical_name(FULL_RERANKER_LOGICAL_NAME)
        reranked_hits = await reranker.rerank(
            query.text,
            list(result.hits),
            top_k=query.top_k,
        )
        result = RetrievalResult(
            query=result.query,
            hits=reranked_hits,
            index_identity=result.index_identity,
            embedding_model=result.embedding_model,
            reranker_model=reranker.identity(),
            warnings=list(result.warnings),
        )

    return result


def result_to_json(result: RetrievalResult) -> str:
    """Serialize a retrieval result as canonical JSON."""
    return (
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2, allow_nan=False)
        + "\n"
    )
