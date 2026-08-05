"""Prefix and embedding validation tests (FakeProvider; no model downloads)."""

from __future__ import annotations

import math

import pytest

from halyk_agent.adapters.embeddings.errors import (
    EmbeddingDependencyMissingError,
    EmbeddingTruncationError,
    EmbeddingValidationError,
)
from halyk_agent.adapters.embeddings.model_registry import (
    FAST_EMBEDDING_LOGICAL_NAME,
    FULL_EMBEDDING_LOGICAL_NAME,
    apply_passage_prefix,
    apply_query_prefix,
    resolve_embedding_identity,
)
from halyk_agent.adapters.embeddings.sentence_transformer_provider import (
    SentenceTransformerEmbeddingProvider,
    ensure_sentence_transformers_available,
    validate_embedding_values,
    vectors_to_embedding_vectors,
)
from halyk_agent.contracts.retrieval import EmbeddingVector
from halyk_agent.domain.embeddings import EmbeddingModelIdentity


class FakeProvider:
    """Minimal EmbeddingProvider stand-in for prefix / identity tests."""

    def __init__(self, identity: EmbeddingModelIdentity) -> None:
        self._identity = identity

    def identity(self) -> EmbeddingModelIdentity:
        return self._identity

    async def prewarm(self) -> None:
        return None

    async def embed_documents(self, texts: list[str]) -> list[EmbeddingVector]:
        dim = self._identity.dimension or 4
        prefixed = [apply_passage_prefix(text, self._identity) for text in texts]
        assert all(
            (not self._identity.passage_prefix) or text.startswith(self._identity.passage_prefix)
            for text in prefixed
        )
        return [
            EmbeddingVector(
                model_id=self._identity.model_id,
                dimensions=dim,
                values=[float(len(text))] + [0.0] * (dim - 1),
            )
            for text in prefixed
        ]

    async def embed_query(self, query: str) -> EmbeddingVector:
        dim = self._identity.dimension or 4
        prefixed = apply_query_prefix(query, self._identity)
        assert (not self._identity.query_prefix) or prefixed.startswith(self._identity.query_prefix)
        return EmbeddingVector(
            model_id=self._identity.model_id,
            dimensions=dim,
            values=[float(len(prefixed))] + [0.0] * (dim - 1),
        )


def test_fast_identity_pinned_with_e5_prefixes() -> None:
    identity = resolve_embedding_identity(FAST_EMBEDDING_LOGICAL_NAME)
    assert identity.model_id == "intfloat/multilingual-e5-small"
    assert identity.revision == "614241f622f53c4eeff9890bdc4f31cfecc418b3"
    assert identity.dimension == 384
    assert identity.query_prefix == "query: "
    assert identity.passage_prefix == "passage: "
    assert identity.license == "MIT"


def test_full_identity_pinned_e5_prefixes() -> None:
    identity = resolve_embedding_identity(FULL_EMBEDDING_LOGICAL_NAME)
    assert identity.model_id == "intfloat/multilingual-e5-small"
    assert identity.revision == "614241f622f53c4eeff9890bdc4f31cfecc418b3"
    assert identity.dimension == 384
    assert identity.query_prefix == "query: "
    assert identity.passage_prefix == "passage: "


@pytest.mark.asyncio
async def test_fake_provider_applies_e5_prefixes() -> None:
    identity = resolve_embedding_identity(FAST_EMBEDDING_LOGICAL_NAME)
    provider = FakeProvider(identity)
    docs = await provider.embed_documents(["hello"])
    query = await provider.embed_query("world")
    assert docs[0].dimensions == 384
    assert query.dimensions == 384
    # prefix length is baked into FakeProvider first component
    assert docs[0].values[0] == float(len("passage: hello"))
    assert query.values[0] == float(len("query: world"))


@pytest.mark.asyncio
async def test_fake_provider_full_uses_e5_prefixes() -> None:
    identity = resolve_embedding_identity(FULL_EMBEDDING_LOGICAL_NAME)
    provider = FakeProvider(identity)
    docs = await provider.embed_documents(["hello"])
    query = await provider.embed_query("world")
    assert docs[0].values[0] == float(len("passage: hello"))
    assert query.values[0] == float(len("query: world"))


def test_apply_prefix_idempotent() -> None:
    identity = resolve_embedding_identity(FAST_EMBEDDING_LOGICAL_NAME)
    once = apply_query_prefix("q", identity)
    twice = apply_query_prefix(once, identity)
    assert once == "query: q"
    assert twice == once


def test_validate_rejects_wrong_dimension() -> None:
    with pytest.raises(EmbeddingValidationError, match="dimension mismatch"):
        validate_embedding_values([0.1, 0.2], expected_dimension=3)


def test_validate_rejects_nan() -> None:
    with pytest.raises(EmbeddingValidationError, match="NaN"):
        validate_embedding_values([0.0, float("nan")], expected_dimension=2)


def test_validate_rejects_inf() -> None:
    with pytest.raises(EmbeddingValidationError, match="Inf"):
        validate_embedding_values([0.0, float("inf")], expected_dimension=2)
    with pytest.raises(EmbeddingValidationError, match="Inf"):
        validate_embedding_values([0.0, float("-inf")], expected_dimension=2)


def test_vectors_to_embedding_vectors_aligns_dimensions() -> None:
    vectors = vectors_to_embedding_vectors(
        [[0.1, 0.2, 0.3]],
        model_id="test-model",
        expected_dimension=3,
    )
    assert vectors[0].dimensions == 3
    assert len(vectors[0].values) == 3
    with pytest.raises(ValueError):
        EmbeddingVector(model_id="x", dimensions=2, values=[1.0, 2.0, 3.0])


def test_missing_sentence_transformers_raises_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "sentence_transformers" or name.startswith("sentence_transformers."):
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(EmbeddingDependencyMissingError):
        ensure_sentence_transformers_available()


@pytest.mark.asyncio
async def test_truncation_raises_when_configured() -> None:
    identity = EmbeddingModelIdentity(
        logical_name="tiny",
        model_id="tiny-model",
        revision="abc",
        dimension=4,
        max_input_tokens=2,
        normalized=True,
        query_prefix="",
        passage_prefix="",
        license="MIT",
    )
    provider = SentenceTransformerEmbeddingProvider(
        identity,
        reject_truncation=True,
        eval_mode=True,
    )

    class _Tok:
        def encode(
            self,
            text: str,
            add_special_tokens: bool = True,
            truncation: bool = True,
        ) -> list[int]:
            del add_special_tokens, truncation
            return list(range(len(text) + 5))

    class _Model:
        tokenizer = _Tok()

        def eval(self) -> None:
            return None

        def encode(self, texts: list[str], **kwargs: object) -> list[list[float]]:
            del kwargs
            return [[0.0, 0.0, 0.0, 0.0] for _ in texts]

    async def fake_ensure() -> object:
        return _Model()

    provider._ensure_model = fake_ensure  # type: ignore[method-assign]
    with pytest.raises(EmbeddingTruncationError):
        await provider.embed_query("long-query-text")


def test_validate_accepts_finite_vector() -> None:
    values = validate_embedding_values([0.25, -0.5, 0.0], expected_dimension=3)
    assert values == [0.25, -0.5, 0.0]
    assert not any(math.isnan(v) or math.isinf(v) for v in values)


def test_provider_from_logical_name_uses_lock() -> None:
    # Uses repo model-lock; construction must not import sentence_transformers yet.
    provider = SentenceTransformerEmbeddingProvider.from_logical_name(
        FAST_EMBEDDING_LOGICAL_NAME,
        cache=None,
    )
    assert provider.identity().logical_name == FAST_EMBEDDING_LOGICAL_NAME
    assert provider.identity().dimension == 384
