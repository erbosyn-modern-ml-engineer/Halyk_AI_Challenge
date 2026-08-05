"""Local content-addressed chunk cache (JSON only, atomic writes)."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path

from halyk_agent.domain.chunking import (
    CHUNK_SCHEMA_VERSION,
    ChunkerIdentity,
    RetrievalChunk,
)
from halyk_agent.domain.ids import deterministic_id


class ChunkCacheError(RuntimeError):
    """Raised for prohibited or corrupt chunk-cache operations."""


def cache_key(
    *,
    document_id: str,
    document_version_id: str,
    chunker: ChunkerIdentity,
) -> str:
    """Content-addressed cache key (hex)."""
    return deterministic_id(
        "chunk-cache-v1",
        document_id,
        document_version_id,
        chunker.name,
        chunker.version,
        chunker.configuration_hash,
        chunker.normalization_version,
        CHUNK_SCHEMA_VERSION,
    )


class LocalChunkCache:
    """Filesystem JSON cache for lists of RetrievalChunk objects."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def get(
        self,
        *,
        document_id: str,
        document_version_id: str,
        chunker: ChunkerIdentity,
    ) -> list[RetrievalChunk] | None:
        """Return validated chunks or None on miss/corrupt entry."""
        key = cache_key(
            document_id=document_id,
            document_version_id=document_version_id,
            chunker=chunker,
        )
        path = self._path_for(key)
        if not path.exists():
            return None
        try:
            raw = path.read_text(encoding="utf-8")
            payload = json.loads(raw)
            if not isinstance(payload, list):
                raise ValueError("chunk cache must be a JSON array")
            return [RetrievalChunk.model_validate(item) for item in payload]
        except (OSError, ValueError, json.JSONDecodeError, TypeError):
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)
            return None

    def put(
        self,
        chunks: list[RetrievalChunk],
        *,
        document_id: str,
        document_version_id: str,
        chunker: ChunkerIdentity,
    ) -> Path:
        """Atomically write chunks into the cache (JSON only)."""
        if any(
            path.suffix.lower() in {".pkl", ".pickle"}
            for path in self.root.rglob("*")
            if path.is_file()
        ):
            raise ChunkCacheError("pickle cache is prohibited")
        key = cache_key(
            document_id=document_id,
            document_version_id=document_version_id,
            chunker=chunker,
        )
        path = self._path_for(key)
        payload = [chunk.model_dump(mode="json") for chunk in chunks]
        text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        self.root.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".chunk-cache-", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise
        return path


def assert_no_pickle_files(root: Path) -> None:
    """Raise if any pickle files exist under the cache root."""
    for path in root.rglob("*"):
        if path.suffix.lower() in {".pkl", ".pickle"}:
            raise ChunkCacheError(f"pickle cache file found: {path.name}")
