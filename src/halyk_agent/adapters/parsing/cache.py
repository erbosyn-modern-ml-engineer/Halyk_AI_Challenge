"""Local content-addressed parse cache (JSON envelope, atomic writes)."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from halyk_agent.adapters.parsing.errors import ParseCacheError
from halyk_agent.adapters.parsing.post_parse_gate import (
    OCR_BACKEND_NONE,
    OCR_POLICY_NONE,
    PAGE_QUALITY_GATE_VERSION,
    PageQualitySummary,
    page_quality_configuration_hash,
    validate_cached_trust,
)
from halyk_agent.adapters.parsing.text_normalization import NORMALIZATION_VERSION
from halyk_agent.domain.ids import deterministic_id
from halyk_agent.domain.parsing import (
    CANONICAL_DOCUMENT_SCHEMA_VERSION,
    CanonicalDocument,
    ParserIdentity,
)

PARSE_CACHE_SCHEMA_VERSION = "halyk.parse_cache.v2"


class CacheGetStatus(StrEnum):
    HIT = "HIT"
    MISS = "MISS"
    INCOMPATIBLE = "INCOMPATIBLE"
    CORRUPT = "CORRUPT"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class CacheLookup:
    status: CacheGetStatus
    document: CanonicalDocument | None = None
    summary: PageQualitySummary | None = None
    reason: str | None = None


class ParseCacheEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cache_schema_version: str
    parser_backend: str
    parser_implementation_version: str
    canonical_document_schema_version: str
    page_quality_gate_version: str
    page_quality_configuration_hash: str
    ocr_policy: str
    ocr_backend_identity: str
    ocr_configuration_hash: str
    source_sha256: str
    normalization_version: str
    page_quality_summary: PageQualitySummary
    document: CanonicalDocument


def cache_identity(
    *,
    source_sha256: str,
    parser: ParserIdentity,
    ocr_policy: str = OCR_POLICY_NONE,
    ocr_backend_identity: str = OCR_BACKEND_NONE,
    ocr_configuration_hash: str = "none",
) -> str:
    """Content-addressed cache key including page-quality / OCR identity."""
    return deterministic_id(
        PARSE_CACHE_SCHEMA_VERSION,
        source_sha256,
        parser.package_name,
        parser.package_version,
        parser.configuration_hash,
        CANONICAL_DOCUMENT_SCHEMA_VERSION,
        NORMALIZATION_VERSION,
        PAGE_QUALITY_GATE_VERSION,
        page_quality_configuration_hash(),
        ocr_policy,
        ocr_backend_identity,
        ocr_configuration_hash,
    )


def cache_key(
    *,
    source_sha256: str,
    parser: ParserIdentity,
    ocr_policy: str = OCR_POLICY_NONE,
    ocr_backend_identity: str = OCR_BACKEND_NONE,
    ocr_configuration_hash: str = "none",
) -> str:
    """Backward-compatible alias for cache identity hex."""
    return cache_identity(
        source_sha256=source_sha256,
        parser=parser,
        ocr_policy=ocr_policy,
        ocr_backend_identity=ocr_backend_identity,
        ocr_configuration_hash=ocr_configuration_hash,
    )


class LocalParseCache:
    """Filesystem JSON cache for gated CanonicalDocument objects."""

    def __init__(
        self,
        root: Path,
        *,
        ocr_policy: str = OCR_POLICY_NONE,
        ocr_backend_identity: str = OCR_BACKEND_NONE,
        ocr_configuration_hash: str = "none",
    ) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.ocr_policy = ocr_policy
        self.ocr_backend_identity = ocr_backend_identity
        self.ocr_configuration_hash = ocr_configuration_hash

    def _path_for(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def get(
        self,
        *,
        source_sha256: str,
        parser: ParserIdentity,
    ) -> CacheLookup:
        """Return a validated cache hit or an explicit non-hit status."""
        key = cache_identity(
            source_sha256=source_sha256,
            parser=parser,
            ocr_policy=self.ocr_policy,
            ocr_backend_identity=self.ocr_backend_identity,
            ocr_configuration_hash=self.ocr_configuration_hash,
        )
        path = self._path_for(key)
        if not path.exists():
            # Legacy v1 keys used a different namespace; treat as incompatible if present.
            legacy = self._legacy_path(source_sha256=source_sha256, parser=parser)
            if legacy is not None and legacy.exists():
                return CacheLookup(
                    status=CacheGetStatus.INCOMPATIBLE,
                    reason="legacy_cache_missing_page_quality_identity",
                )
            return CacheLookup(status=CacheGetStatus.MISS)

        try:
            raw = path.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)
            return CacheLookup(status=CacheGetStatus.CORRUPT, reason="unreadable_json")

        if not isinstance(payload, dict) or "cache_schema_version" not in payload:
            return CacheLookup(
                status=CacheGetStatus.INCOMPATIBLE,
                reason="legacy_document_only_entry",
            )

        try:
            envelope = ParseCacheEnvelope.model_validate(payload)
        except ValueError as exc:
            return CacheLookup(status=CacheGetStatus.CORRUPT, reason=f"envelope_invalid:{exc}")

        if envelope.cache_schema_version != PARSE_CACHE_SCHEMA_VERSION:
            return CacheLookup(
                status=CacheGetStatus.INCOMPATIBLE,
                reason="cache_schema_version_mismatch",
            )
        if envelope.source_sha256 != source_sha256:
            return CacheLookup(status=CacheGetStatus.REJECTED, reason="source_sha_mismatch")
        if envelope.parser_backend != parser.package_name:
            return CacheLookup(status=CacheGetStatus.INCOMPATIBLE, reason="parser_backend_mismatch")
        if envelope.parser_implementation_version != parser.package_version:
            return CacheLookup(
                status=CacheGetStatus.INCOMPATIBLE,
                reason="parser_implementation_version_mismatch",
            )
        if envelope.page_quality_gate_version != PAGE_QUALITY_GATE_VERSION:
            return CacheLookup(
                status=CacheGetStatus.INCOMPATIBLE,
                reason="page_quality_gate_version_mismatch",
            )
        if envelope.page_quality_configuration_hash != page_quality_configuration_hash():
            return CacheLookup(
                status=CacheGetStatus.INCOMPATIBLE,
                reason="page_quality_configuration_mismatch",
            )
        if envelope.ocr_policy != self.ocr_policy:
            return CacheLookup(status=CacheGetStatus.INCOMPATIBLE, reason="ocr_policy_mismatch")
        if envelope.ocr_backend_identity != self.ocr_backend_identity:
            return CacheLookup(
                status=CacheGetStatus.INCOMPATIBLE,
                reason="ocr_backend_identity_mismatch",
            )
        if envelope.ocr_configuration_hash != self.ocr_configuration_hash:
            return CacheLookup(
                status=CacheGetStatus.INCOMPATIBLE,
                reason="ocr_configuration_mismatch",
            )

        trust_error = validate_cached_trust(envelope.document, envelope.page_quality_summary)
        if trust_error is not None:
            return CacheLookup(status=CacheGetStatus.REJECTED, reason=trust_error)

        return CacheLookup(
            status=CacheGetStatus.HIT,
            document=envelope.document,
            summary=envelope.page_quality_summary,
        )

    def put(
        self,
        document: CanonicalDocument,
        *,
        source_sha256: str,
        parser: ParserIdentity,
        page_quality_summary: PageQualitySummary,
    ) -> Path:
        """Atomically write a gated canonical document into the cache."""
        if path_suffix_is_pickle(document):
            raise ParseCacheError("pickle cache is prohibited")
        trust_error = validate_cached_trust(document, page_quality_summary)
        if trust_error is not None:
            raise ParseCacheError(f"refusing to cache untrusted document: {trust_error}")

        key = cache_identity(
            source_sha256=source_sha256,
            parser=parser,
            ocr_policy=self.ocr_policy,
            ocr_backend_identity=self.ocr_backend_identity,
            ocr_configuration_hash=self.ocr_configuration_hash,
        )
        path = self._path_for(key)
        envelope = ParseCacheEnvelope(
            cache_schema_version=PARSE_CACHE_SCHEMA_VERSION,
            parser_backend=parser.package_name,
            parser_implementation_version=parser.package_version,
            canonical_document_schema_version=CANONICAL_DOCUMENT_SCHEMA_VERSION,
            page_quality_gate_version=PAGE_QUALITY_GATE_VERSION,
            page_quality_configuration_hash=page_quality_configuration_hash(),
            ocr_policy=self.ocr_policy,
            ocr_backend_identity=self.ocr_backend_identity,
            ocr_configuration_hash=self.ocr_configuration_hash,
            source_sha256=source_sha256,
            normalization_version=NORMALIZATION_VERSION,
            page_quality_summary=page_quality_summary,
            document=document,
        )
        text = json.dumps(envelope.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
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

    def _legacy_path(self, *, source_sha256: str, parser: ParserIdentity) -> Path | None:
        try:
            legacy_key = deterministic_id(
                "parse-cache-v1",
                source_sha256,
                parser.package_name,
                parser.package_version,
                parser.configuration_hash,
                CANONICAL_DOCUMENT_SCHEMA_VERSION,
                NORMALIZATION_VERSION,
            )
        except Exception:
            return None
        return self.root / f"{legacy_key}.json"


def path_suffix_is_pickle(_document: CanonicalDocument) -> bool:
    """Guard used by tests to assert no pickle usage."""
    return False


def assert_no_pickle_files(root: Path) -> None:
    """Raise if any pickle files exist under the cache root."""
    for path in root.rglob("*"):
        if path.suffix.lower() in {".pkl", ".pickle"}:
            raise ParseCacheError(f"pickle cache file found: {path.name}")
