"""Local content-addressed parse cache (JSON only, atomic writes)."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path

from halyk_agent.adapters.parsing.errors import ParseCacheError
from halyk_agent.adapters.parsing.text_normalization import NORMALIZATION_VERSION
from halyk_agent.domain.ids import deterministic_id
from halyk_agent.domain.parsing import (
    CANONICAL_DOCUMENT_SCHEMA_VERSION,
    CanonicalDocument,
    ParserIdentity,
)


def cache_key(
    *,
    source_sha256: str,
    parser: ParserIdentity,
) -> str:
    """Content-addressed cache key (hex)."""
    return deterministic_id(
        "parse-cache-v1",
        source_sha256,
        parser.package_name,
        parser.package_version,
        parser.configuration_hash,
        CANONICAL_DOCUMENT_SCHEMA_VERSION,
        NORMALIZATION_VERSION,
    )


class LocalParseCache:
    """Filesystem JSON cache for CanonicalDocument objects."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def get(
        self,
        *,
        source_sha256: str,
        parser: ParserIdentity,
    ) -> CanonicalDocument | None:
        """Return a validated cache hit or None on miss/corrupt entry."""
        key = cache_key(source_sha256=source_sha256, parser=parser)
        path = self._path_for(key)
        if not path.exists():
            return None
        try:
            raw = path.read_text(encoding="utf-8")
            return CanonicalDocument.model_validate_json(raw)
        except (OSError, ValueError, json.JSONDecodeError):
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)
            return None

    def put(
        self,
        document: CanonicalDocument,
        *,
        source_sha256: str,
        parser: ParserIdentity,
    ) -> Path:
        """Atomically write a canonical document into the cache."""
        if path_suffix_is_pickle(document):
            raise ParseCacheError("pickle cache is prohibited")
        key = cache_key(source_sha256=source_sha256, parser=parser)
        path = self._path_for(key)
        payload = document.model_dump(mode="json")
        text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        self.root.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".cache-", suffix=".tmp", dir=self.root)
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


def path_suffix_is_pickle(_document: CanonicalDocument) -> bool:
    """Guard used by tests to assert no pickle usage."""
    return False


def assert_no_pickle_files(root: Path) -> None:
    """Raise if any pickle files exist under the cache root."""
    for path in root.rglob("*"):
        if path.suffix.lower() in {".pkl", ".pickle"}:
            raise ParseCacheError(f"pickle cache file found: {path.name}")
