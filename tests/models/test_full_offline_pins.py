"""Pinned model identity checks (Stage 4.2 competition defaults)."""

from __future__ import annotations

import pytest

from halyk_agent.adapters.embeddings.model_registry import (
    FULL_EMBEDDING_LOGICAL_NAME,
    FULL_RERANKER_LOGICAL_NAME,
    OPTIONAL_BGE_M3_LOGICAL_NAME,
    resolve_embedding_identity,
)

pytestmark = pytest.mark.retrieval_models


def test_full_embedding_is_e5_small() -> None:
    identity = resolve_embedding_identity(FULL_EMBEDDING_LOGICAL_NAME)
    assert identity.model_id == "intfloat/multilingual-e5-small"
    assert identity.revision == "614241f622f53c4eeff9890bdc4f31cfecc418b3"
    assert identity.dimension == 384
    assert identity.license == "MIT"
    assert identity.query_prefix == "query: "
    assert identity.passage_prefix == "passage: "


def test_optional_bge_m3_still_pinned_but_not_default() -> None:
    identity = resolve_embedding_identity(OPTIONAL_BGE_M3_LOGICAL_NAME)
    assert identity.model_id == "BAAI/bge-m3"
    assert identity.revision == "5617a9f61b028005a4858fdac845db406aefb181"
    assert identity.dimension == 1024


def test_bge_reranker_identity_pinned_optional() -> None:
    identity = resolve_embedding_identity(FULL_RERANKER_LOGICAL_NAME)
    assert identity.model_id == "BAAI/bge-reranker-v2-m3"
    assert identity.revision == "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
    assert identity.license == "Apache-2.0"
