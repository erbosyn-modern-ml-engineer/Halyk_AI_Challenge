"""Load pinned embedding / reranker identities from model-lock.json."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from halyk_agent.adapters.embeddings.errors import EmbeddingModelNotFoundError
from halyk_agent.domain.embeddings import EmbeddingModelIdentity

FAST_EMBEDDING_LOGICAL_NAME = "fast-embedding"
FULL_EMBEDDING_LOGICAL_NAME = "full-embedding"
OPTIONAL_BGE_M3_LOGICAL_NAME = "optional-bge-m3"
FULL_RERANKER_LOGICAL_NAME = "full-reranker"

# Authoritative competition embedding (FULL profile default).
COMPETITION_EMBEDDING_LOGICAL_NAME = FULL_EMBEDDING_LOGICAL_NAME

_EMBEDDING_LOGICAL_NAMES = frozenset(
    {
        FAST_EMBEDDING_LOGICAL_NAME,
        FULL_EMBEDDING_LOGICAL_NAME,
        OPTIONAL_BGE_M3_LOGICAL_NAME,
    }
)


def default_model_lock_path() -> Path:
    """Locate model-lock.json by walking up from this file / CWD."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "model-lock.json"
        if candidate.is_file():
            return candidate
    cwd_candidate = Path.cwd() / "model-lock.json"
    if cwd_candidate.is_file():
        return cwd_candidate
    raise EmbeddingModelNotFoundError("model-lock.json not found (expected at repository root)")


def _entry_to_identity(entry: dict[str, Any], *, normalized: bool) -> EmbeddingModelIdentity:
    dimension = entry.get("dimension")
    max_tokens = entry.get("max_input_tokens")
    return EmbeddingModelIdentity(
        logical_name=str(entry["logical_name"]),
        model_id=str(entry["repository_or_model_id"]),
        revision=str(entry["revision"]),
        dimension=int(dimension) if dimension is not None else None,
        max_input_tokens=int(max_tokens) if max_tokens is not None else None,
        normalized=normalized,
        query_prefix=str(entry.get("query_prefix") or ""),
        passage_prefix=str(entry.get("passage_prefix") or ""),
        license=str(entry["license"]),
    )


def load_model_lock(path: Path | None = None) -> list[dict[str, Any]]:
    """Load raw model-lock entries (no timestamps expected)."""
    lock_path = path or default_model_lock_path()
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EmbeddingModelNotFoundError(
            f"failed to load model-lock.json: {exc.__class__.__name__}"
        ) from exc
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list) or not models:
        raise EmbeddingModelNotFoundError("model-lock.json has no models")
    return [dict(item) for item in models if isinstance(item, dict)]


@lru_cache(maxsize=4)
def _cached_identities(lock_path_str: str) -> dict[str, EmbeddingModelIdentity]:
    entries = load_model_lock(Path(lock_path_str))
    identities: dict[str, EmbeddingModelIdentity] = {}
    for entry in entries:
        logical = str(entry["logical_name"])
        is_embedding = logical in _EMBEDDING_LOGICAL_NAMES
        # Reranker and other non-embeddings: normalized=False
        identities[logical] = _entry_to_identity(entry, normalized=is_embedding)
    return identities


def resolve_embedding_identity(
    logical_name: str,
    *,
    model_lock_path: Path | None = None,
    normalized: bool | None = None,
) -> EmbeddingModelIdentity:
    """Resolve a pinned EmbeddingModelIdentity by logical_name."""
    lock_path = model_lock_path or default_model_lock_path()
    identities = _cached_identities(str(lock_path.resolve()))
    try:
        identity = identities[logical_name]
    except KeyError as exc:
        known = ", ".join(sorted(identities))
        raise EmbeddingModelNotFoundError(
            f"unknown logical model name {logical_name!r}; known: {known}"
        ) from exc
    if normalized is None or normalized == identity.normalized:
        return identity
    return identity.model_copy(update={"normalized": normalized})


def default_embedding_logical_name(profile: str) -> str:
    """Authoritative embedding logical name for a profile (FULL → E5-small)."""
    profile_norm = profile.lower().strip()
    if profile_norm == "fast":
        return FAST_EMBEDDING_LOGICAL_NAME
    return FULL_EMBEDDING_LOGICAL_NAME


def apply_passage_prefix(text: str, identity: EmbeddingModelIdentity) -> str:
    """Apply passage/document prefix from model identity (may be empty)."""
    prefix = identity.passage_prefix
    if not prefix:
        return text
    if text.startswith(prefix):
        return text
    return f"{prefix}{text}"


def apply_query_prefix(text: str, identity: EmbeddingModelIdentity) -> str:
    """Apply query prefix from model identity (may be empty)."""
    prefix = identity.query_prefix
    if not prefix:
        return text
    if text.startswith(prefix):
        return text
    return f"{prefix}{text}"


__all__ = [
    "COMPETITION_EMBEDDING_LOGICAL_NAME",
    "FAST_EMBEDDING_LOGICAL_NAME",
    "FULL_EMBEDDING_LOGICAL_NAME",
    "FULL_RERANKER_LOGICAL_NAME",
    "OPTIONAL_BGE_M3_LOGICAL_NAME",
    "apply_passage_prefix",
    "apply_query_prefix",
    "default_embedding_logical_name",
    "default_model_lock_path",
    "load_model_lock",
    "resolve_embedding_identity",
]
