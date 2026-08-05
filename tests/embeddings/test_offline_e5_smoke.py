"""Offline cached E5-small smoke (skips when cache missing)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from halyk_agent.adapters.embeddings.model_registry import (
    FULL_EMBEDDING_LOGICAL_NAME,
    resolve_embedding_identity,
)
from halyk_agent.adapters.embeddings.sentence_transformer_provider import (
    SentenceTransformerEmbeddingProvider,
)

pytestmark = pytest.mark.retrieval_models


def _e5_cache_present() -> bool:
    hub = Path.home() / ".cache" / "huggingface" / "hub"
    return (hub / "models--intfloat--multilingual-e5-small").is_dir()


@pytest.mark.asyncio
async def test_offline_cached_e5_embedding_dimension() -> None:
    if not _e5_cache_present():
        pytest.skip("E5-small HF cache not present")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["TORCHDYNAMO_DISABLE"] = "1"
    os.environ["TORCH_COMPILE_DISABLE"] = "1"
    identity = resolve_embedding_identity(FULL_EMBEDDING_LOGICAL_NAME)
    provider = SentenceTransformerEmbeddingProvider(identity, batch_size=1)
    vector = await provider.embed_query("лимит по договору")
    assert vector.dimensions == 384
    assert len(vector.values) == 384
