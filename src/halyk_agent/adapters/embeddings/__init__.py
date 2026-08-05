"""Embedding adapters (Sentence-Transformers providers, model lock, cache)."""

from __future__ import annotations

from halyk_agent.adapters.embeddings.cache import LocalEmbeddingCache, embedding_cache_key
from halyk_agent.adapters.embeddings.errors import (
    EmbeddingCacheError,
    EmbeddingDependencyMissingError,
    EmbeddingError,
    EmbeddingModelNotFoundError,
    EmbeddingTruncationError,
    EmbeddingValidationError,
)
from halyk_agent.adapters.embeddings.model_registry import (
    FAST_EMBEDDING_LOGICAL_NAME,
    FULL_EMBEDDING_LOGICAL_NAME,
    FULL_RERANKER_LOGICAL_NAME,
    apply_passage_prefix,
    apply_query_prefix,
    load_model_lock,
    resolve_embedding_identity,
)
from halyk_agent.adapters.embeddings.sentence_transformer_provider import (
    SentenceTransformerEmbeddingProvider,
    ensure_sentence_transformers_available,
    validate_embedding_values,
)

__all__ = [
    "FAST_EMBEDDING_LOGICAL_NAME",
    "FULL_EMBEDDING_LOGICAL_NAME",
    "FULL_RERANKER_LOGICAL_NAME",
    "EmbeddingCacheError",
    "EmbeddingDependencyMissingError",
    "EmbeddingError",
    "EmbeddingModelNotFoundError",
    "EmbeddingTruncationError",
    "EmbeddingValidationError",
    "LocalEmbeddingCache",
    "SentenceTransformerEmbeddingProvider",
    "apply_passage_prefix",
    "apply_query_prefix",
    "embedding_cache_key",
    "ensure_sentence_transformers_available",
    "load_model_lock",
    "resolve_embedding_identity",
    "validate_embedding_values",
]
