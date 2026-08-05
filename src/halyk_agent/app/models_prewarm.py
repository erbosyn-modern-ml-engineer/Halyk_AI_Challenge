"""Model prewarm application helpers."""

from __future__ import annotations

import os
from pathlib import Path

from halyk_agent.adapters.embeddings.model_registry import (
    FAST_EMBEDDING_LOGICAL_NAME,
    FULL_EMBEDDING_LOGICAL_NAME,
    FULL_RERANKER_LOGICAL_NAME,
    resolve_embedding_identity,
)
from halyk_agent.adapters.embeddings.sentence_transformer_provider import (
    SentenceTransformerEmbeddingProvider,
)


async def prewarm_components(*, profile: str, components: list[str]) -> list[str]:
    """Explicitly download/load models; returns human-readable status lines."""
    profile_norm = profile.lower().strip()
    lines: list[str] = []
    wanted = {item.strip().lower() for item in components}
    hub_cache = Path(
        os.environ.get("HF_HOME")
        or os.environ.get("HUGGINGFACE_HUB_CACHE")
        or (Path.home() / ".cache" / "huggingface" / "hub")
    )
    lines.append(f"huggingface_hub_cache={hub_cache}")
    if "embeddings" in wanted:
        logical = (
            FAST_EMBEDDING_LOGICAL_NAME if profile_norm == "fast" else FULL_EMBEDDING_LOGICAL_NAME
        )
        identity = resolve_embedding_identity(logical)
        provider = SentenceTransformerEmbeddingProvider(identity)
        await provider.prewarm()
        lines.append(
            f"embeddings ready: {identity.model_id}@{identity.revision} dim={identity.dimension}"
        )
    if "reranker" in wanted:
        if profile_norm != "full":
            lines.append("reranker skipped: only valid for FULL profile")
        else:
            from halyk_agent.adapters.reranking.cross_encoder import CrossEncoderReranker

            reranker = CrossEncoderReranker.from_logical_name(FULL_RERANKER_LOGICAL_NAME)
            await reranker.prewarm()
            identity = resolve_embedding_identity(FULL_RERANKER_LOGICAL_NAME)
            lines.append(f"reranker ready: {identity.model_id}@{identity.revision}")
    if "parser" in wanted and profile_norm == "full":
        from halyk_agent.adapters.parsing.docling_parser import ensure_docling_available

        ensure_docling_available()
        lines.append("parser dependency docling importable")
    if len(lines) == 1:
        lines.append("no components prewarmed")
    return lines
