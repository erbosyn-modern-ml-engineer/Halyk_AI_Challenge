"""Canonical document parsing domain models."""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from halyk_agent.domain.common import JsonObject, NonEmptyStr
from halyk_agent.domain.ids import deterministic_id, sha256_text

CANONICAL_DOCUMENT_SCHEMA_VERSION = "halyk.canonical_document.v1"
NORMALIZATION_VERSION = "halyk.text_normalization.v1"
BBOX_PAGE_TOLERANCE = 1e-3


class ParserKind(StrEnum):
    """Concrete parser backend identity."""

    PYPDF = "PYPDF"
    DOCLING = "DOCLING"
    PLAIN_TEXT = "PLAIN_TEXT"


class ParseStatus(StrEnum):
    """Outcome status for a parse attempt or selected document."""

    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    UNSUPPORTED = "UNSUPPORTED"
    ENCRYPTED = "ENCRYPTED"
    QUALITY_REJECTED = "QUALITY_REJECTED"


class BlockKind(StrEnum):
    """Semantic or structural block kinds justified by a parser."""

    TITLE = "TITLE"
    HEADING = "HEADING"
    PARAGRAPH = "PARAGRAPH"
    LIST_ITEM = "LIST_ITEM"
    TABLE = "TABLE"
    TABLE_CELL = "TABLE_CELL"
    CAPTION = "CAPTION"
    HEADER = "HEADER"
    FOOTER = "FOOTER"
    PAGE_TEXT = "PAGE_TEXT"
    UNKNOWN = "UNKNOWN"


class CoordinateOrigin(StrEnum):
    """Bounding-box coordinate origin."""

    TOP_LEFT = "TOP_LEFT"
    BOTTOM_LEFT = "BOTTOM_LEFT"


class QualityDecision(StrEnum):
    """Deterministic parse-quality routing decision."""

    ACCEPT = "ACCEPT"
    FALLBACK_REQUIRED = "FALLBACK_REQUIRED"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    REJECT = "REJECT"


class ParseWarningCode(StrEnum):
    """Bounded warning codes for parse operations."""

    EMPTY_PAGE = "EMPTY_PAGE"
    MALFORMED_PAGE = "MALFORMED_PAGE"
    EXTRACT_TEXT_NONE = "EXTRACT_TEXT_NONE"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    MISSING_PROVENANCE = "MISSING_PROVENANCE"
    INVALID_BBOX = "INVALID_BBOX"
    ENCRYPTED = "ENCRYPTED"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    PARTIAL_CONTENT = "PARTIAL_CONTENT"
    QUALITY_SIGNAL = "QUALITY_SIGNAL"
    DEPENDENCY_MISSING = "DEPENDENCY_MISSING"
    PARSER_ERROR = "PARSER_ERROR"
    CACHE_CORRUPT = "CACHE_CORRUPT"
    OTHER = "OTHER"


class CanonicalBoundingBox(BaseModel):
    """Page-relative bounding box in canonical TOP_LEFT coordinates."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    left: float
    top: float
    right: float
    bottom: float
    page_width: float = Field(gt=0.0)
    page_height: float = Field(gt=0.0)
    origin: CoordinateOrigin = CoordinateOrigin.TOP_LEFT

    @field_validator("left", "top", "right", "bottom", "page_width", "page_height")
    @classmethod
    def _finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("bounding box values must be finite")
        return value

    @model_validator(mode="after")
    def _invariants(self) -> CanonicalBoundingBox:
        if self.origin is not CoordinateOrigin.TOP_LEFT:
            raise ValueError("canonical bounding boxes must use TOP_LEFT origin")
        if not (self.left < self.right):
            raise ValueError("left must be less than right")
        if not (self.top < self.bottom):
            raise ValueError("top must be less than bottom in TOP_LEFT coordinates")
        tol = BBOX_PAGE_TOLERANCE
        if self.left < -tol or self.top < -tol:
            raise ValueError("bounding box extends outside page bounds")
        if self.right > self.page_width + tol or self.bottom > self.page_height + tol:
            raise ValueError("bounding box extends outside page bounds")
        return self

    def canonical_token(self) -> str:
        """Stable serialization for deterministic IDs."""
        return (
            f"{self.left:.6f},{self.top:.6f},{self.right:.6f},{self.bottom:.6f},"
            f"{self.page_width:.6f},{self.page_height:.6f},{self.origin.value}"
        )


class CanonicalBlock(BaseModel):
    """A text block with exact offsets into page raw text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: NonEmptyStr
    page_number: int = Field(ge=1)
    ordinal: int = Field(ge=0)
    kind: BlockKind
    raw_text: str
    normalized_text: str
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)
    bbox: CanonicalBoundingBox | None = None
    source_parser: ParserKind
    source_item_id: NonEmptyStr | None = None
    metadata: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def _offsets(self) -> CanonicalBlock:
        start, end = self.char_start, self.char_end
        if (start is None) ^ (end is None):
            raise ValueError("char_start and char_end must both exist or both be absent")
        if start is not None and end is not None and start > end:
            raise ValueError("char_start must be <= char_end (half-open)")
        if start is not None and end is not None and start == end and self.raw_text:
            raise ValueError("non-empty raw_text requires a non-empty character range")
        return self


class CanonicalTableCell(BaseModel):
    """One table cell with zero-based indexes and preserved raw text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: NonEmptyStr
    page_number: int = Field(ge=1)
    table_id: NonEmptyStr
    row_index: int = Field(ge=0)
    column_index: int = Field(ge=0)
    row_span: int = Field(ge=1)
    column_span: int = Field(ge=1)
    raw_text: str
    normalized_text: str
    bbox: CanonicalBoundingBox | None = None
    source_item_id: NonEmptyStr | None = None


class CanonicalTable(BaseModel):
    """Structured table with optional multi-page provenance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: NonEmptyStr
    page_numbers: list[int] = Field(min_length=1)
    ordinal: int = Field(ge=0)
    cells: list[CanonicalTableCell] = Field(default_factory=list)
    row_count: int = Field(ge=0)
    column_count: int = Field(ge=0)
    bbox: CanonicalBoundingBox | None = None
    caption: str | None = None
    source_item_id: NonEmptyStr | None = None

    @field_validator("page_numbers")
    @classmethod
    def _pages(cls, value: list[int]) -> list[int]:
        if any(page < 1 for page in value):
            raise ValueError("page numbers must be 1-based")
        return sorted(set(value))

    @model_validator(mode="after")
    def _sort_cells(self) -> CanonicalTable:
        sorted_cells = sorted(
            self.cells,
            key=lambda cell: (cell.row_index, cell.column_index, cell.id),
        )
        object.__setattr__(self, "cells", sorted_cells)
        return self


class CanonicalPage(BaseModel):
    """One document page with deterministic block/table ordering."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    page_number: int = Field(ge=1)
    width: float | None = Field(default=None, gt=0.0)
    height: float | None = Field(default=None, gt=0.0)
    raw_text: str = ""
    normalized_text: str = ""
    blocks: list[CanonicalBlock] = Field(default_factory=list)
    tables: list[CanonicalTable] = Field(default_factory=list)
    warnings: list[NonEmptyStr] = Field(default_factory=list)

    @field_validator("width", "height")
    @classmethod
    def _finite_dims(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("page dimensions must be finite")
        return value

    @model_validator(mode="after")
    def _page_invariants(self) -> CanonicalPage:
        if (self.width is None) ^ (self.height is None):
            raise ValueError("page width and height must both exist or both be absent")
        for block in self.blocks:
            if block.page_number != self.page_number:
                raise ValueError("block page_number must match page")
            if block.bbox is not None and (self.width is None or self.height is None):
                raise ValueError("bbox requires page dimensions")
            if (
                block.char_start is not None
                and block.char_end is not None
                and self.raw_text[block.char_start : block.char_end] != block.raw_text
            ):
                raise ValueError("block char range must equal raw_text substring")
        for table in self.tables:
            if self.page_number not in table.page_numbers:
                raise ValueError("table page_numbers must include owning page")
            if table.bbox is not None and (self.width is None or self.height is None):
                raise ValueError("table bbox requires page dimensions")
        sorted_blocks = sorted(self.blocks, key=lambda b: (b.ordinal, b.id))
        sorted_tables = sorted(self.tables, key=lambda t: (t.ordinal, t.id))
        object.__setattr__(self, "blocks", sorted_blocks)
        object.__setattr__(self, "tables", sorted_tables)
        return self


class ParserIdentity(BaseModel):
    """Identity of the parser configuration that produced a document."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ParserKind
    package_name: NonEmptyStr
    package_version: NonEmptyStr
    configuration_hash: NonEmptyStr


class ParseMetrics(BaseModel):
    """Deterministic parse quality metrics (no NaN/Inf)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    page_count: int = Field(ge=0)
    nonempty_page_count: int = Field(ge=0)
    empty_page_ratio: float = Field(ge=0.0, le=1.0)
    total_character_count: int = Field(ge=0)
    alphanumeric_character_ratio: float = Field(ge=0.0, le=1.0)
    replacement_character_ratio: float = Field(ge=0.0, le=1.0)
    control_character_count: int = Field(ge=0)
    duplicate_line_ratio: float = Field(ge=0.0, le=1.0)
    pages_without_text: int = Field(ge=0)
    table_count: int = Field(ge=0)
    block_count: int = Field(ge=0)

    @field_validator(
        "empty_page_ratio",
        "alphanumeric_character_ratio",
        "replacement_character_ratio",
        "duplicate_line_ratio",
    )
    @classmethod
    def _finite_ratio(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("metrics must be finite")
        return value


class ParseWarning(BaseModel):
    """Structured parse warning."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: ParseWarningCode
    message: NonEmptyStr
    page_number: int | None = Field(default=None, ge=1)
    source_item_id: NonEmptyStr | None = None


class CanonicalDocument(BaseModel):
    """Canonical parsed document without nondeterministic timestamps."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: NonEmptyStr
    artifact_id: NonEmptyStr
    document_id: NonEmptyStr
    document_version_id: NonEmptyStr
    source_file: NonEmptyStr
    source_sha256: NonEmptyStr
    mime_type: NonEmptyStr | None = None
    parser: ParserIdentity
    status: ParseStatus
    pages: list[CanonicalPage] = Field(default_factory=list)
    metrics: ParseMetrics
    warnings: list[ParseWarning] = Field(default_factory=list)
    schema_version: NonEmptyStr = CANONICAL_DOCUMENT_SCHEMA_VERSION

    @model_validator(mode="after")
    def _document_invariants(self) -> CanonicalDocument:
        if self.status is ParseStatus.SUCCESS and not self.pages:
            raise ValueError("SUCCESS requires at least one page")
        if self.status is ParseStatus.FAILED and any(page.raw_text.strip() for page in self.pages):
            raise ValueError("FAILED must not contain trusted page content")
        sorted_pages = sorted(self.pages, key=lambda page: page.page_number)
        object.__setattr__(self, "pages", sorted_pages)
        return self


class ParseAttempt(BaseModel):
    """One parser attempt including operational timing."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    parser: ParserIdentity
    status: ParseStatus
    metrics: ParseMetrics | None = None
    warnings: list[ParseWarning] = Field(default_factory=list)
    error_code: NonEmptyStr | None = None
    error_message: NonEmptyStr | None = None
    duration_ms: int = Field(ge=0)


class ParseResult(BaseModel):
    """Selected parse result for one artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: NonEmptyStr
    selected_document: CanonicalDocument | None = None
    attempts: list[ParseAttempt] = Field(default_factory=list)
    quality_decision: QualityDecision
    cache_hit: bool = False


class ParseBatchReport(BaseModel):
    """Batch parse report for an inspection directory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: NonEmptyStr = "halyk.parse_batch_report.v1"
    profile: NonEmptyStr
    total_candidates: int = Field(ge=0)
    successful: int = Field(ge=0)
    partial: int = Field(ge=0)
    failed: int = Field(ge=0)
    unsupported: int = Field(ge=0)
    cache_hits: int = Field(ge=0)
    results: list[ParseResult] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sort_results(self) -> ParseBatchReport:
        sorted_results = sorted(self.results, key=lambda item: item.artifact_id)
        object.__setattr__(self, "results", sorted_results)
        return self


def document_identity(artifact_id: str, source_sha256: str) -> str:
    """Deterministic document identity independent of parser configuration."""
    return deterministic_id("canonical-document-v1", artifact_id, source_sha256)


def document_version_identity(
    artifact_id: str,
    source_sha256: str,
    parser: ParserIdentity,
) -> str:
    """Parse-run identity that incorporates parser configuration."""
    return deterministic_id(
        "canonical-document-version-v1",
        artifact_id,
        source_sha256,
        parser.kind.value,
        parser.package_name,
        parser.package_version,
        parser.configuration_hash,
    )


def block_identity(
    document_id: str,
    page_number: int,
    ordinal: int,
    kind: BlockKind,
    raw_text: str,
    bbox: CanonicalBoundingBox | None,
) -> str:
    """Deterministic block ID."""
    return deterministic_id(
        "canonical-block-v1",
        document_id,
        page_number,
        ordinal,
        kind.value,
        sha256_text(raw_text),
        bbox.canonical_token() if bbox is not None else "",
    )


def table_identity(
    document_id: str,
    ordinal: int,
    page_numbers: list[int],
    source_item_id: str | None,
) -> str:
    """Deterministic table ID."""
    pages = ",".join(str(p) for p in sorted(set(page_numbers)))
    return deterministic_id(
        "canonical-table-v1",
        document_id,
        ordinal,
        pages,
        source_item_id or "",
    )


def table_cell_identity(
    table_id: str,
    row_index: int,
    column_index: int,
    row_span: int,
    column_span: int,
    raw_text: str,
) -> str:
    """Deterministic table-cell ID."""
    return deterministic_id(
        "canonical-table-cell-v1",
        table_id,
        row_index,
        column_index,
        row_span,
        column_span,
        sha256_text(raw_text),
    )


def empty_metrics() -> ParseMetrics:
    """Zeroed metrics for failed/unsupported attempts."""
    return ParseMetrics(
        page_count=0,
        nonempty_page_count=0,
        empty_page_ratio=0.0,
        total_character_count=0,
        alphanumeric_character_ratio=0.0,
        replacement_character_ratio=0.0,
        control_character_count=0,
        duplicate_line_ratio=0.0,
        pages_without_text=0,
        table_count=0,
        block_count=0,
    )


def compute_metrics(pages: list[CanonicalPage]) -> ParseMetrics:
    """Compute parse metrics from canonical pages."""
    page_count = len(pages)
    nonempty = sum(1 for page in pages if page.raw_text.strip())
    pages_without_text = page_count - nonempty
    empty_ratio = (pages_without_text / page_count) if page_count else 0.0
    all_text = "".join(page.raw_text for page in pages)
    total_chars = len(all_text)
    alnum = sum(1 for ch in all_text if ch.isalnum())
    replacement = all_text.count("\ufffd")
    control = sum(1 for ch in all_text if ord(ch) < 32 and ch not in "\t\n\r")
    lines = [line for page in pages for line in page.raw_text.splitlines()]
    if lines:
        unique = len(set(lines))
        duplicate_line_ratio = 1.0 - (unique / len(lines))
    else:
        duplicate_line_ratio = 0.0
    table_count = sum(len(page.tables) for page in pages)
    block_count = sum(len(page.blocks) for page in pages)
    return ParseMetrics(
        page_count=page_count,
        nonempty_page_count=nonempty,
        empty_page_ratio=empty_ratio,
        total_character_count=total_chars,
        alphanumeric_character_ratio=(alnum / total_chars) if total_chars else 0.0,
        replacement_character_ratio=(replacement / total_chars) if total_chars else 0.0,
        control_character_count=control,
        duplicate_line_ratio=duplicate_line_ratio,
        pages_without_text=pages_without_text,
        table_count=table_count,
        block_count=block_count,
    )


def configuration_hash(payload: dict[str, Any]) -> str:
    """Hash a sorted JSON-like configuration mapping."""
    import json

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256_text(canonical)
