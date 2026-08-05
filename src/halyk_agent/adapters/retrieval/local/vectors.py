"""Float32 embedding BLOB packing and brute-force cosine search."""

from __future__ import annotations

import hashlib
import math
import struct
from collections.abc import Iterable, Sequence

from halyk_agent.adapters.retrieval.errors import (
    CorruptEmbeddingBlobError,
    EmbeddingDimensionError,
)

_FLOAT32_SIZE = 4


def pack_float32_vector(values: Sequence[float]) -> tuple[bytes, int, str]:
    """Pack a finite float vector as little-endian float32 bytes with checksum.

    Returns:
        ``(blob, dimension, sha256_hex)``.
    """
    if not values:
        raise EmbeddingDimensionError("embedding vector must be non-empty")
    floats: list[float] = []
    for index, value in enumerate(values):
        number = float(value)
        if not math.isfinite(number):
            raise EmbeddingDimensionError(f"embedding value at index {index} must be finite")
        floats.append(number)
    dimension = len(floats)
    blob = struct.pack("<" + ("f" * dimension), *floats)
    checksum = hashlib.sha256(blob).hexdigest()
    return blob, dimension, checksum


def unpack_float32_vector(
    blob: bytes,
    *,
    dimension: int,
    checksum: str,
) -> list[float]:
    """Unpack and validate a float32 embedding BLOB (rejects corruption)."""
    if dimension < 1:
        raise EmbeddingDimensionError("stored embedding dimension must be >= 1")
    expected_len = dimension * _FLOAT32_SIZE
    if len(blob) != expected_len:
        raise CorruptEmbeddingBlobError(
            f"embedding blob length {len(blob)} does not match dimension {dimension}"
        )
    actual = hashlib.sha256(blob).hexdigest()
    if actual != checksum:
        raise CorruptEmbeddingBlobError("embedding blob checksum mismatch")
    try:
        values = struct.unpack("<" + ("f" * dimension), blob)
    except struct.error as exc:
        raise CorruptEmbeddingBlobError("embedding blob is not valid float32 data") from exc
    return [float(v) for v in values]


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Cosine similarity; zero when either vector has zero magnitude."""
    if len(left) != len(right):
        raise EmbeddingDimensionError("cosine similarity requires equal dimensions")
    dot = 0.0
    left_norm_sq = 0.0
    right_norm_sq = 0.0
    for a, b in zip(left, right, strict=True):
        dot += a * b
        left_norm_sq += a * a
        right_norm_sq += b * b
    if left_norm_sq == 0.0 or right_norm_sq == 0.0:
        return 0.0
    return dot / (math.sqrt(left_norm_sq) * math.sqrt(right_norm_sq))


def brute_force_cosine_topk(
    query: Sequence[float],
    candidates: Iterable[tuple[str, Sequence[float]]],
    *,
    top_k: int,
) -> list[tuple[str, float]]:
    """Score filtered candidates with cosine similarity; return top_k descending.

    Tie-break: higher score first, then ``chunk_id`` ascending.
    """
    if top_k < 1:
        raise ValueError("top_k must be >= 1")
    scored: list[tuple[str, float]] = []
    for chunk_id, vector in candidates:
        scored.append((chunk_id, cosine_similarity(query, vector)))
    scored.sort(key=lambda item: (-item[1], item[0]))
    return scored[:top_k]


__all__ = [
    "brute_force_cosine_topk",
    "cosine_similarity",
    "pack_float32_vector",
    "unpack_float32_vector",
]
