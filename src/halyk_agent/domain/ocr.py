"""Selective OCR domain models (Stage 5A.4)."""

from __future__ import annotations

import math
import re
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from halyk_agent.domain.common import NonEmptyStr
from halyk_agent.domain.ids import deterministic_id, sha256_text
from halyk_agent.domain.page_quality import PageQualityState

REQUIRED_OCR_LANGUAGES: tuple[str, ...] = ("eng", "rus", "kaz")
DEFAULT_MAX_SELECTED_PAGES = 32
MAX_OCR_BLOCK_CHARS = 200_000
BBox = Annotated[tuple[float, float, float, float], Field(min_length=4, max_length=4)]


class OcrBackendKind(StrEnum):
    TESSERACT_CLI = "TESSERACT_CLI"
    RAPIDOCR_LOCAL = "RAPIDOCR_LOCAL"
    MOCK = "MOCK"
    NONE = "NONE"


class TextOrigin(StrEnum):
    EMBEDDED_PDF_TEXT = "EMBEDDED_PDF_TEXT"
    DOCLING_EXTRACTED_TEXT = "DOCLING_EXTRACTED_TEXT"
    OCR = "OCR"


class OcrFailureReason(StrEnum):
    BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
    LANGUAGE_UNAVAILABLE = "LANGUAGE_UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    EMPTY_OUTPUT = "EMPTY_OUTPUT"
    LOW_QUALITY = "LOW_QUALITY"
    RENDER_FAILED = "RENDER_FAILED"
    SUBPROCESS_FAILED = "SUBPROCESS_FAILED"
    UTF8_DECODE_FAILED = "UTF8_DECODE_FAILED"
    INVALID_PROVENANCE = "INVALID_PROVENANCE"
    PAGE_NOT_SELECTED = "PAGE_NOT_SELECTED"
    OTHER = "OTHER"


class OcrPageStatus(StrEnum):
    OCR_SUCCEEDED = "OCR_SUCCEEDED"
    OCR_LOW_QUALITY = "OCR_LOW_QUALITY"
    OCR_FAILED = "OCR_FAILED"
    OCR_TIMEOUT = "OCR_TIMEOUT"
    OCR_BACKEND_UNAVAILABLE = "OCR_BACKEND_UNAVAILABLE"
    OCR_LANGUAGE_UNAVAILABLE = "OCR_LANGUAGE_UNAVAILABLE"
    SKIPPED = "SKIPPED"


class OcrBackendIdentity(BaseModel):
    """Exact OCR backend configuration identity for cache and provenance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: OcrBackendKind
    backend_version: NonEmptyStr
    executable_or_package: NonEmptyStr
    language_data_identity: NonEmptyStr
    languages: list[NonEmptyStr] = Field(min_length=1)
    render_scale: float = Field(gt=0.0)
    page_segmentation_mode: int = Field(ge=0, le=13)
    configuration_hash: NonEmptyStr

    @field_validator("render_scale")
    @classmethod
    def _finite_scale(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("render_scale must be finite")
        return value

    def identity_token(self) -> str:
        langs = "+".join(sorted(self.languages))
        return (
            f"{self.kind.value}|{self.backend_version}|{self.executable_or_package}|"
            f"{self.language_data_identity}|{langs}|s={self.render_scale:.4f}|"
            f"psm={self.page_segmentation_mode}|cfg={self.configuration_hash}"
        )


class OcrBackendAvailability(BaseModel):
    """Probe result for one OCR backend candidate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: OcrBackendKind
    installed: bool
    offline_ready: bool
    version: str | None = None
    executable_path: str | None = None
    language_data_path: str | None = None
    installed_languages: list[str] = Field(default_factory=list)
    required_languages: list[str] = Field(default_factory=lambda: list(REQUIRED_OCR_LANGUAGES))
    missing_languages: list[str] = Field(default_factory=list)
    missing_components: list[str] = Field(default_factory=list)
    network_required: bool = False
    may_download: bool = False
    measured_local_artifact_bytes: int | None = Field(default=None, ge=0)
    notes: list[str] = Field(default_factory=list)


class OcrProbeReport(BaseModel):
    """Aggregated OCR environment probe."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: NonEmptyStr = "halyk.ocr_probe.v1"
    candidates: list[OcrBackendAvailability]
    selected_kind: OcrBackendKind = OcrBackendKind.NONE
    offline_ready_backend: bool = False
    downloads_performed: bool = False
    docling_version: str | None = None
    other_packages: dict[str, str | None] = Field(default_factory=dict)


class OcrPageSelection(BaseModel):
    """One page selected for selective OCR."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_path: NonEmptyStr
    source_sha256: NonEmptyStr
    document_id: NonEmptyStr
    document_version_id: NonEmptyStr
    artifact_id: NonEmptyStr
    page_number: int = Field(ge=1)
    page_quality_state: PageQualityState
    reason: NonEmptyStr


class OcrPageRequest(BaseModel):
    """Backend request for one selected page."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_path: NonEmptyStr
    source_sha256: NonEmptyStr
    document_id: NonEmptyStr
    document_version_id: NonEmptyStr
    page_number: int = Field(ge=1)
    reason: NonEmptyStr
    page_quality_state: PageQualityState
    languages: list[NonEmptyStr] = Field(min_length=1)


class OcrTextBlock(BaseModel):
    """One OCR text observation with required provenance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: NonEmptyStr
    page_number: int = Field(ge=1)
    bbox: BBox | None = None
    reading_order: int = Field(ge=0)
    confidence: float | None = None
    origin: TextOrigin = TextOrigin.OCR
    backend: OcrBackendIdentity
    source_image_identity: NonEmptyStr

    @field_validator("text")
    @classmethod
    def _non_empty_bounded(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("OCR block text must be non-empty")
        if len(stripped) > MAX_OCR_BLOCK_CHARS:
            raise ValueError("OCR block text exceeds bounded maximum")
        return stripped

    @field_validator("confidence")
    @classmethod
    def _finite_confidence(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if not math.isfinite(value):
            raise ValueError("OCR confidence must be finite")
        if value < 0.0 or value > 1.0:
            raise ValueError("OCR confidence must be in [0, 1]")
        return value

    @field_validator("bbox")
    @classmethod
    def _finite_bbox(cls, value: BBox | None) -> BBox | None:
        if value is None:
            return None
        if any(not math.isfinite(c) for c in value):
            raise ValueError("OCR bbox coordinates must be finite")
        return value

    @model_validator(mode="after")
    def _origin_must_be_ocr(self) -> OcrTextBlock:
        if self.origin is not TextOrigin.OCR:
            raise ValueError("OcrTextBlock.origin must be OCR")
        return self


class OcrPageResult(BaseModel):
    """OCR outcome for one requested page."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request: OcrPageRequest
    status: OcrPageStatus
    blocks: list[OcrTextBlock] = Field(default_factory=list)
    failure_reason: OcrFailureReason | None = None
    message: str | None = None
    duration_ms: int = Field(ge=0, default=0)
    temporary_bytes_written: int = Field(ge=0, default=0)
    temporary_cleanup_ok: bool = True

    @model_validator(mode="after")
    def _success_requires_blocks(self) -> OcrPageResult:
        if self.status is OcrPageStatus.OCR_SUCCEEDED and not self.blocks:
            raise ValueError("OCR_SUCCEEDED requires at least one block")
        for block in self.blocks:
            if block.page_number != self.request.page_number:
                raise ValueError("OCR block page_number must match request")
        return self


class OcrDocumentResult(BaseModel):
    """OCR outcomes for one source document."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: NonEmptyStr
    document_version_id: NonEmptyStr
    source_sha256: NonEmptyStr
    page_results: list[OcrPageResult] = Field(default_factory=list)


class OcrPlan(BaseModel):
    """Deterministic selective OCR plan."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: NonEmptyStr = "halyk.ocr_plan.v1"
    only_required: bool = True
    max_pages: int = Field(ge=1)
    override_active: bool = False
    selections: list[OcrPageSelection] = Field(default_factory=list)
    total_pdfs: int = Field(ge=0, default=0)
    total_pages: int = Field(ge=0, default=0)
    blocking_pages: int = Field(ge=0, default=0)


class OcrRunReport(BaseModel):
    """Authoritative selective OCR run report."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: NonEmptyStr = "halyk.ocr_run_report.v1"
    backend: OcrBackendIdentity | None = None
    probe: OcrProbeReport
    plan: OcrPlan
    selected_pages: int = Field(ge=0)
    attempted_pages: int = Field(ge=0)
    succeeded_pages: int = Field(ge=0)
    failed_pages: int = Field(ge=0)
    remaining_blocking_pages: int = Field(ge=0)
    cache_hits: int = Field(ge=0, default=0)
    cache_misses: int = Field(ge=0, default=0)
    temporary_bytes_written: int = Field(ge=0, default=0)
    persistent_cache_bytes_written: int = Field(ge=0, default=0)
    cleanup_failures: int = Field(ge=0, default=0)
    page_results: list[OcrPageResult] = Field(default_factory=list)
    documents_processed: int = Field(ge=0, default=0)
    offline_ready: bool = False
    blocked_reason: str | None = None


def ocr_configuration_hash(
    *,
    languages: list[str],
    render_scale: float,
    page_segmentation_mode: int,
    extra: str = "",
) -> str:
    """Deterministic OCR configuration identity."""
    payload = (
        f"langs={'+'.join(sorted(languages))}|scale={render_scale:.4f}|"
        f"psm={page_segmentation_mode}|extra={extra}"
    )
    return sha256_text(payload)[:32]


def ocr_cache_identity(
    *,
    source_sha256: str,
    page_number: int,
    backend: OcrBackendIdentity,
) -> str:
    """Content-addressed OCR page cache key."""
    return deterministic_id(
        # v2: UTF-8-strict decode contract; do not reuse v1 mojibake cache entries.
        "halyk.ocr_cache.v2",
        source_sha256,
        page_number,
        backend.kind.value,
        backend.backend_version,
        backend.language_data_identity,
        "+".join(sorted(backend.languages)),
        f"{backend.render_scale:.4f}",
        backend.page_segmentation_mode,
        backend.configuration_hash,
    )


_REPEAT_RE = re.compile(r"(.)\1{12,}")


def validate_ocr_page_text(text: str) -> OcrPageStatus:
    """Validate OCR text quality; success requires usable extract."""
    stripped = text.strip()
    if not stripped:
        return OcrPageStatus.OCR_FAILED
    if len(stripped) < 12:
        return OcrPageStatus.OCR_LOW_QUALITY
    alnum = sum(1 for ch in stripped if ch.isalnum())
    ratio = alnum / len(stripped)
    if ratio < 0.12:
        return OcrPageStatus.OCR_LOW_QUALITY
    repl = stripped.count("\ufffd") / len(stripped)
    if repl > 0.15:
        return OcrPageStatus.OCR_LOW_QUALITY
    if _REPEAT_RE.search(stripped):
        return OcrPageStatus.OCR_LOW_QUALITY
    return OcrPageStatus.OCR_SUCCEEDED
