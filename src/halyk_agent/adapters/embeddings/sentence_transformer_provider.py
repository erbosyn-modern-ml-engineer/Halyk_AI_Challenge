"""Sentence-Transformers embedding provider (lazy import, thread offload)."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from halyk_agent.adapters.embeddings.cache import LocalEmbeddingCache
from halyk_agent.adapters.embeddings.errors import (
    EmbeddingDependencyMissingError,
    EmbeddingTruncationError,
    EmbeddingValidationError,
)
from halyk_agent.adapters.embeddings.model_registry import (
    FAST_EMBEDDING_LOGICAL_NAME,
    apply_passage_prefix,
    apply_query_prefix,
    resolve_embedding_identity,
)
from halyk_agent.contracts.retrieval import EmbeddingVector
from halyk_agent.domain.embeddings import EmbeddingModelIdentity

logger = logging.getLogger(__name__)


def ensure_sentence_transformers_available() -> None:
    """Raise a typed error when sentence-transformers is not installed."""
    try:
        import sentence_transformers  # noqa: F401
    except ImportError as exc:  # pragma: no cover - exercised via monkeypatch
        raise EmbeddingDependencyMissingError(
            "sentence-transformers is not installed. Install with: uv sync --extra retrieval-fast"
        ) from exc


def validate_embedding_values(
    values: Sequence[float],
    *,
    expected_dimension: int,
) -> list[float]:
    """Reject wrong dimensions, NaN, and Infinity. Return a plain float list."""
    if len(values) != expected_dimension:
        raise EmbeddingValidationError(
            f"embedding dimension mismatch: got {len(values)}, expected {expected_dimension}"
        )
    out: list[float] = []
    for index, raw in enumerate(values):
        value = float(raw)
        if math.isnan(value):
            raise EmbeddingValidationError(f"embedding contains NaN at index {index}")
        if math.isinf(value):
            raise EmbeddingValidationError(f"embedding contains Inf at index {index}")
        out.append(value)
    return out


def vectors_to_embedding_vectors(
    rows: Sequence[Sequence[float]],
    *,
    model_id: str,
    expected_dimension: int,
) -> list[EmbeddingVector]:
    """Validate and wrap raw rows as EmbeddingVector payloads."""
    result: list[EmbeddingVector] = []
    for row in rows:
        values = validate_embedding_values(row, expected_dimension=expected_dimension)
        result.append(
            EmbeddingVector(
                model_id=model_id,
                dimensions=expected_dimension,
                values=values,
            )
        )
    return result


class SentenceTransformerEmbeddingProvider:
    """Async EmbeddingProvider backed by sentence-transformers (sync encode offloaded)."""

    def __init__(
        self,
        identity: EmbeddingModelIdentity,
        *,
        cache: LocalEmbeddingCache | None = None,
        batch_size: int = 32,
        device: str | None = None,
        normalize: bool | None = None,
        reject_truncation: bool = False,
        eval_mode: bool = True,
        trust_remote_code: bool = False,
    ) -> None:
        if identity.dimension is None:
            raise EmbeddingValidationError(
                f"embedding model {identity.logical_name!r} has no pinned dimension"
            )
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        self._identity = identity.model_copy(
            update={"normalized": identity.normalized if normalize is None else normalize}
        )
        self._cache = cache
        self._batch_size = batch_size
        self._device = device
        self._reject_truncation = reject_truncation
        self._eval_mode = eval_mode
        self._trust_remote_code = trust_remote_code
        self._model: Any | None = None
        self._load_lock = asyncio.Lock()

    @classmethod
    def from_logical_name(
        cls,
        logical_name: str = FAST_EMBEDDING_LOGICAL_NAME,
        *,
        model_lock_path: Path | None = None,
        cache: LocalEmbeddingCache | None = None,
        batch_size: int = 32,
        device: str | None = None,
        normalize: bool | None = None,
        reject_truncation: bool = False,
        eval_mode: bool = True,
        trust_remote_code: bool = False,
    ) -> SentenceTransformerEmbeddingProvider:
        """Build a provider from a model-lock logical name (FAST/FULL embeddings)."""
        identity = resolve_embedding_identity(
            logical_name,
            model_lock_path=model_lock_path,
            normalized=normalize,
        )
        return cls(
            identity,
            cache=cache,
            batch_size=batch_size,
            device=device,
            normalize=normalize,
            reject_truncation=reject_truncation,
            eval_mode=eval_mode,
            trust_remote_code=trust_remote_code,
        )

    def identity(self) -> EmbeddingModelIdentity:
        """Return pinned embedding model identity."""
        return self._identity

    async def prewarm(self) -> None:
        """Load model weights explicitly (call before enabling offline env flags)."""
        await self._ensure_model()

    async def embed_documents(self, texts: list[str]) -> list[EmbeddingVector]:
        """Embed passage/document texts with passage prefixes when configured."""
        if not texts:
            return []
        prefixed = [apply_passage_prefix(text, self._identity) for text in texts]
        return await self._embed_prefixed(prefixed)

    async def embed_query(self, query: str) -> EmbeddingVector:
        """Embed a query with query prefix when configured."""
        prefixed = apply_query_prefix(query, self._identity)
        vectors = await self._embed_prefixed([prefixed])
        return vectors[0]

    async def _embed_prefixed(self, prefixed_texts: list[str]) -> list[EmbeddingVector]:
        expected_dim = self._identity.dimension
        assert expected_dim is not None

        results: list[EmbeddingVector | None] = [None] * len(prefixed_texts)
        missing_indices: list[int] = []
        missing_texts: list[str] = []

        if self._cache is not None:
            for index, text in enumerate(prefixed_texts):
                cached = self._cache.get(
                    model_id=self._identity.model_id,
                    revision=self._identity.revision,
                    prefixed_text=text,
                    expected_dimension=expected_dim,
                )
                if cached is None:
                    missing_indices.append(index)
                    missing_texts.append(text)
                else:
                    values = validate_embedding_values(cached, expected_dimension=expected_dim)
                    results[index] = EmbeddingVector(
                        model_id=self._identity.model_id,
                        dimensions=expected_dim,
                        values=values,
                    )
        else:
            missing_indices = list(range(len(prefixed_texts)))
            missing_texts = list(prefixed_texts)

        if missing_texts:
            encoded = await self._encode(missing_texts)
            for offset, index in enumerate(missing_indices):
                row = encoded[offset]
                values = validate_embedding_values(row, expected_dimension=expected_dim)
                vector = EmbeddingVector(
                    model_id=self._identity.model_id,
                    dimensions=expected_dim,
                    values=values,
                )
                results[index] = vector
                if self._cache is not None:
                    self._cache.put(
                        values,
                        model_id=self._identity.model_id,
                        revision=self._identity.revision,
                        prefixed_text=missing_texts[offset],
                    )

        completed: list[EmbeddingVector] = []
        for item in results:
            if item is None:
                raise EmbeddingValidationError("internal embedding result hole")
            completed.append(item)
        return completed

    async def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        async with self._load_lock:
            if self._model is not None:
                return self._model
            ensure_sentence_transformers_available()
            self._model = await asyncio.to_thread(self._load_model_sync)
            return self._model

    def _load_model_sync(self) -> Any:
        from sentence_transformers import SentenceTransformer

        kwargs: dict[str, Any] = {
            "trust_remote_code": self._trust_remote_code,
        }
        if self._device is not None:
            kwargs["device"] = self._device
        # revision is passed via model_kwargs / revision where supported
        model = SentenceTransformer(
            self._identity.model_id,
            revision=self._identity.revision,
            **kwargs,
        )
        if self._eval_mode:
            model.eval()
            try:
                import torch

                torch.manual_seed(0)
                if torch.cuda.is_available():
                    torch.cuda.manual_seed_all(0)
            except ImportError:  # pragma: no cover
                pass
        return model

    async def _encode(self, prefixed_texts: list[str]) -> list[list[float]]:
        model = await self._ensure_model()
        await asyncio.to_thread(self._check_truncation, model, prefixed_texts)
        return await asyncio.to_thread(self._encode_sync, model, prefixed_texts)

    def _check_truncation(self, model: Any, prefixed_texts: list[str]) -> None:
        tokenizer = getattr(model, "tokenizer", None)
        max_tokens = self._identity.max_input_tokens
        if tokenizer is None or max_tokens is None:
            return
        for text in prefixed_texts:
            # truncation=False so we observe true length (no silent clip here)
            encoded = tokenizer.encode(text, add_special_tokens=True, truncation=False)
            length = len(encoded)
            if length <= max_tokens:
                continue
            message = (
                f"input token length {length} exceeds max_input_tokens {max_tokens} "
                f"for model {self._identity.model_id}"
            )
            if self._reject_truncation:
                raise EmbeddingTruncationError(message)
            logger.warning("embedding truncation risk: %s", message)

    def _encode_sync(self, model: Any, prefixed_texts: list[str]) -> list[list[float]]:
        vectors = model.encode(
            prefixed_texts,
            batch_size=self._batch_size,
            normalize_embeddings=self._identity.normalized,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [[float(x) for x in row] for row in vectors]


__all__ = [
    "SentenceTransformerEmbeddingProvider",
    "ensure_sentence_transformers_available",
    "validate_embedding_values",
    "vectors_to_embedding_vectors",
]
