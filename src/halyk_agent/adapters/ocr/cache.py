"""Content-addressed OCR page-result cache (JSON, atomic writes)."""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from halyk_agent.domain.ocr import OcrBackendIdentity, OcrPageResult, ocr_cache_identity

OCR_CACHE_SCHEMA = "halyk.ocr_page_cache.v2"


class OcrCacheEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cache_schema_version: str = OCR_CACHE_SCHEMA
    identity: str
    result: OcrPageResult


class LocalOcrPageCache:
    """Page-level OCR cache; failed results are not cached."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.bytes_written = 0
        self.hits = 0
        self.misses = 0

    def _path(self, identity: str) -> Path:
        return self.root / f"{identity}.json"

    def get(
        self,
        *,
        source_sha256: str,
        page_number: int,
        backend: OcrBackendIdentity,
    ) -> OcrPageResult | None:
        identity = ocr_cache_identity(
            source_sha256=source_sha256,
            page_number=page_number,
            backend=backend,
        )
        path = self._path(identity)
        if not path.is_file():
            self.misses += 1
            return None
        try:
            envelope = OcrCacheEnvelope.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            self.misses += 1
            with contextlib.suppress(OSError):
                path.unlink()
            return None
        if envelope.identity != identity:
            self.misses += 1
            return None
        self.hits += 1
        return envelope.result

    def put(
        self,
        result: OcrPageResult,
        *,
        backend: OcrBackendIdentity,
    ) -> None:
        from halyk_agent.domain.ocr import OcrPageStatus

        if result.status is not OcrPageStatus.OCR_SUCCEEDED:
            return
        identity = ocr_cache_identity(
            source_sha256=result.request.source_sha256,
            page_number=result.request.page_number,
            backend=backend,
        )
        envelope = OcrCacheEnvelope(identity=identity, result=result)
        payload = envelope.model_dump_json() + "\n"
        path = self._path(identity)
        fd, tmp_name = tempfile.mkstemp(prefix=".ocr-cache-", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
            os.replace(tmp_name, path)
            self.bytes_written += len(payload.encode("utf-8"))
        finally:
            with contextlib.suppress(OSError):
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
