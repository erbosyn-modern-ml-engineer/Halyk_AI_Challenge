"""Streaming SHA-256 helpers."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Compute SHA-256 of a file by streaming chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    """Compute SHA-256 of an in-memory byte string."""
    return hashlib.sha256(data).hexdigest()


def iter_file_chunks(path: Path, *, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
    """Yield file chunks for streaming hash or copy operations."""
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            yield chunk


def artifact_id_for(normalized_path: str, content_sha256: str) -> str:
    """Deterministic artifact ID from normalized path and content hash."""
    material = f"{normalized_path}|{content_sha256}".encode()
    return hashlib.sha256(material).hexdigest()
