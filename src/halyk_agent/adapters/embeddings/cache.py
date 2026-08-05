"""Local embedding cache: JSON metadata + NumPy .npy vectors (atomic, no pickle)."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from halyk_agent.adapters.embeddings.errors import EmbeddingCacheError
from halyk_agent.domain.ids import deterministic_id


def embedding_cache_key(
    *,
    model_id: str,
    revision: str,
    prefixed_text: str,
) -> str:
    """Content-addressed cache key including revision and exact prefixed input."""
    return deterministic_id(
        "embedding-cache-v1",
        model_id,
        revision,
        prefixed_text,
    )


def vector_checksum(values: list[float] | Any) -> str:
    """SHA-256 over float32 little-endian bytes (deterministic)."""
    import numpy as np
    from numpy.typing import NDArray

    arr: NDArray[np.float32] = np.asarray(values, dtype=np.float32)
    return hashlib.sha256(arr.tobytes(order="C")).hexdigest()


class LocalEmbeddingCache:
    """Filesystem cache storing JSON metadata alongside .npy float32 vectors."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _meta_path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def _vector_path(self, key: str) -> Path:
        return self.root / f"{key}.npy"

    def get(
        self,
        *,
        model_id: str,
        revision: str,
        prefixed_text: str,
        expected_dimension: int | None = None,
    ) -> list[float] | None:
        """Return a validated float vector or None on miss/corrupt entry."""
        import numpy as np

        key = embedding_cache_key(
            model_id=model_id,
            revision=revision,
            prefixed_text=prefixed_text,
        )
        meta_path = self._meta_path(key)
        vector_path = self._vector_path(key)
        if not meta_path.exists() or not vector_path.exists():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if not isinstance(meta, dict):
                raise ValueError("metadata must be object")
            if meta.get("model_id") != model_id or meta.get("revision") != revision:
                raise ValueError("model identity mismatch")
            arr = np.load(vector_path, allow_pickle=False)
            if arr.ndim != 1:
                raise ValueError("vector must be 1-D")
            values = [float(x) for x in arr.tolist()]
            dim = int(meta.get("dimension", len(values)))
            if expected_dimension is not None and dim != expected_dimension:
                raise ValueError("dimension mismatch")
            if dim != len(values):
                raise ValueError("dimension does not match vector length")
            checksum = meta.get("vector_checksum")
            if checksum != vector_checksum(values):
                raise ValueError("vector checksum mismatch")
            return values
        except (OSError, ValueError, json.JSONDecodeError, TypeError):
            with contextlib.suppress(OSError):
                meta_path.unlink(missing_ok=True)
            with contextlib.suppress(OSError):
                vector_path.unlink(missing_ok=True)
            return None

    def put(
        self,
        values: list[float],
        *,
        model_id: str,
        revision: str,
        prefixed_text: str,
    ) -> Path:
        """Atomically write metadata JSON and float32 .npy vector."""
        import numpy as np
        from numpy.typing import NDArray

        assert_no_pickle_files(self.root)
        key = embedding_cache_key(
            model_id=model_id,
            revision=revision,
            prefixed_text=prefixed_text,
        )
        meta_path = self._meta_path(key)
        vector_path = self._vector_path(key)
        arr: NDArray[np.float32] = np.asarray(values, dtype=np.float32)
        meta: dict[str, Any] = {
            "model_id": model_id,
            "revision": revision,
            "dimension": int(arr.shape[0]),
            "prefixed_text_sha256": hashlib.sha256(prefixed_text.encode("utf-8")).hexdigest(),
            "vector_checksum": vector_checksum(arr),
        }
        text = json.dumps(meta, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        self.root.mkdir(parents=True, exist_ok=True)

        fd, tmp_meta = tempfile.mkstemp(prefix=".emb-meta-", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            fd_v, tmp_vec = tempfile.mkstemp(prefix=".emb-vec-", suffix=".tmp", dir=self.root)
            try:
                os.close(fd_v)
                with open(tmp_vec, "wb") as handle:
                    np.save(handle, arr, allow_pickle=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp_meta, meta_path)
                os.replace(tmp_vec, vector_path)
            except Exception:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_vec)
                raise
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp_meta)
            raise
        return vector_path


def assert_no_pickle_files(root: Path) -> None:
    """Raise if any pickle files exist under the cache root."""
    if not root.exists():
        return
    for path in root.rglob("*"):
        if path.suffix.lower() in {".pkl", ".pickle"}:
            raise EmbeddingCacheError(f"pickle cache file found: {path.name}")


__all__ = [
    "LocalEmbeddingCache",
    "assert_no_pickle_files",
    "embedding_cache_key",
    "vector_checksum",
]
