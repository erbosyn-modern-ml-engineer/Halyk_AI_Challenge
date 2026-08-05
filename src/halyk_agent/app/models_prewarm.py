"""Model prewarm application helpers."""

from __future__ import annotations

import os
from pathlib import Path

from halyk_agent.adapters.embeddings.download_budget import assert_prewarm_allowed
from halyk_agent.adapters.embeddings.model_registry import (
    FAST_EMBEDDING_LOGICAL_NAME,
    FULL_EMBEDDING_LOGICAL_NAME,
    FULL_RERANKER_LOGICAL_NAME,
    default_embedding_logical_name,
    resolve_embedding_identity,
)
from halyk_agent.adapters.embeddings.sentence_transformer_provider import (
    SentenceTransformerEmbeddingProvider,
)


async def prewarm_components(
    *,
    profile: str,
    components: list[str],
    approve_large_models: bool = False,
) -> list[str]:
    """Explicitly download/load models; returns human-readable status lines.

    Large optional models (BGE-M3, BGE reranker) are refused unless
    ``approve_large_models`` or ``HALYK_ALLOW_LARGE_MODEL_DOWNLOAD=1``.
    """
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
        logical = default_embedding_logical_name(profile_norm)
        # Default FULL/FAST competition path uses E5-small (within budget).
        assert_prewarm_allowed(logical, explicit_approval=approve_large_models)
        identity = resolve_embedding_identity(logical)
        provider = SentenceTransformerEmbeddingProvider(identity)
        await provider.prewarm()
        lines.append(
            f"embeddings ready: {identity.model_id}@{identity.revision} dim={identity.dimension}"
        )
        lines.append(f"embeddings_logical_name={logical}")
    if "reranker" in wanted:
        if profile_norm != "full":
            lines.append("reranker skipped: only valid for FULL profile")
        else:
            assert_prewarm_allowed(
                FULL_RERANKER_LOGICAL_NAME,
                explicit_approval=approve_large_models,
            )
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
    # Silence unused legacy names (still exported for callers/tests).
    _ = (FAST_EMBEDDING_LOGICAL_NAME, FULL_EMBEDDING_LOGICAL_NAME)
    return lines
