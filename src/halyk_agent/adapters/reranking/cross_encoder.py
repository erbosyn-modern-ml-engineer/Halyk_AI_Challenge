"""Cross-encoder reranker (BAAI/bge-reranker-v2-m3) with lazy model import."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from halyk_agent.adapters.embeddings.model_registry import (
    FULL_RERANKER_LOGICAL_NAME,
    resolve_embedding_identity,
)
from halyk_agent.adapters.reranking.errors import (
    RerankingDependencyMissingError,
    RerankingValidationError,
)
from halyk_agent.domain.embeddings import EmbeddingModelIdentity
from halyk_agent.domain.retrieval import MatchedBy, RetrievalHit

logger = logging.getLogger(__name__)


def ensure_cross_encoder_available() -> None:
    """Raise a typed error when sentence-transformers is not installed."""
    try:
        import sentence_transformers  # noqa: F401
    except ImportError as exc:  # pragma: no cover - exercised via monkeypatch
        raise RerankingDependencyMissingError(
            "sentence-transformers is not installed. Install with: uv sync --extra retrieval-full"
        ) from exc


class CrossEncoderReranker:
    """Optional cross-encoder reranker; disabled path never imports the model.

    Original RRF scores on hits are retained; ``rerank_score`` / ``matched_by``
    are updated only when reranking runs.
    """

    def __init__(
        self,
        identity: EmbeddingModelIdentity | None = None,
        *,
        enabled: bool = True,
        model_lock_path: Path | None = None,
        device: str | None = None,
        batch_size: int = 32,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        if identity is None and enabled:
            identity = resolve_embedding_identity(
                FULL_RERANKER_LOGICAL_NAME,
                model_lock_path=model_lock_path,
                normalized=False,
            )
        self._identity = identity
        self._enabled = enabled
        self._device = device
        self._batch_size = batch_size
        self._model: Any | None = None
        self._load_lock = asyncio.Lock()

    @classmethod
    def from_logical_name(
        cls,
        logical_name: str = FULL_RERANKER_LOGICAL_NAME,
        *,
        enabled: bool = True,
        model_lock_path: Path | None = None,
        device: str | None = None,
        batch_size: int = 32,
    ) -> CrossEncoderReranker:
        identity = resolve_embedding_identity(
            logical_name,
            model_lock_path=model_lock_path,
            normalized=False,
        )
        return cls(
            identity,
            enabled=enabled,
            model_lock_path=model_lock_path,
            device=device,
            batch_size=batch_size,
        )

    @property
    def enabled(self) -> bool:
        return self._enabled

    def identity(self) -> EmbeddingModelIdentity | None:
        return self._identity

    async def prewarm(self) -> None:
        """Load model weights when enabled (no-op when disabled)."""
        if not self._enabled:
            return
        await self._ensure_model()

    async def rerank(
        self,
        query: str,
        hits: list[RetrievalHit],
        *,
        top_k: int | None = None,
    ) -> list[RetrievalHit]:
        """Rerank hits; retain original ``rrf_score`` values.

        When disabled, returns the input hits truncated to ``top_k`` without
        importing or loading any model.
        """
        if top_k is not None and top_k < 1:
            raise RerankingValidationError("top_k must be >= 1")
        limit = len(hits) if top_k is None else min(top_k, len(hits))
        if not self._enabled:
            return list(hits[:limit])

        stripped = query.strip()
        if not stripped:
            raise RerankingValidationError("query text must be non-empty")
        if not hits:
            return []

        model = await self._ensure_model()
        pairs = [(stripped, hit.chunk.retrieval_text) for hit in hits]
        scores = await asyncio.to_thread(self._predict, model, pairs)

        ranked = sorted(
            zip(hits, scores, strict=True),
            key=lambda item: (-float(item[1]), item[0].chunk.id),
        )
        results: list[RetrievalHit] = []
        for final_rank, (hit, score) in enumerate(ranked[:limit], start=1):
            results.append(
                hit.model_copy(
                    update={
                        "rerank_score": float(score),
                        "final_rank": final_rank,
                        "matched_by": MatchedBy.RERANKED,
                        # rrf_score intentionally retained from the hybrid hit
                    }
                )
            )
        return results

    async def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        async with self._load_lock:
            if self._model is not None:
                return self._model
            ensure_cross_encoder_available()
            if self._identity is None:
                raise RerankingValidationError("reranker identity is required when enabled")
            from sentence_transformers import CrossEncoder

            logger.info(
                "Loading cross-encoder %s@%s",
                self._identity.model_id,
                self._identity.revision,
            )
            kwargs: dict[str, Any] = {}
            if self._device is not None:
                kwargs["device"] = self._device
            self._model = await asyncio.to_thread(
                CrossEncoder,
                self._identity.model_id,
                revision=self._identity.revision,
                **kwargs,
            )
            return self._model

    def _predict(self, model: Any, pairs: list[tuple[str, str]]) -> list[float]:
        raw = model.predict(pairs, batch_size=self._batch_size, show_progress_bar=False)
        return [float(score) for score in raw]


__all__ = [
    "CrossEncoderReranker",
    "ensure_cross_encoder_available",
]
