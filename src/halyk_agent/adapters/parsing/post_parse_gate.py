"""Backend-independent post-parse quality gate (authoritative trust status)."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from halyk_agent.domain.page_quality import PageQualityState, diagnose_canonical_page
from halyk_agent.domain.parsing import (
    CanonicalDocument,
    ParseStatus,
    ParseWarning,
    ParseWarningCode,
    compute_metrics,
)

PAGE_QUALITY_GATE_VERSION = "halyk.page_quality_gate.v1"
OCR_POLICY_NONE = "ocr_disabled"
OCR_BACKEND_NONE = "NONE"


class PageQualityIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    page_number: int = Field(ge=1)
    state: PageQualityState
    reason_code: str
    char_count: int
    image_count: int


class PageQualitySummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    gate_version: str = PAGE_QUALITY_GATE_VERSION
    page_states: list[PageQualityState]
    issues: list[PageQualityIssue] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class PostParseGateResult:
    document: CanonicalDocument
    summary: PageQualitySummary


_BLOCKING_STATES = frozenset(
    {
        PageQualityState.OCR_REQUIRED,
        PageQualityState.IMAGE_DOMINANT,
        PageQualityState.HEADING_WITHOUT_BODY,
        PageQualityState.UNREADABLE,
    }
)


def blocks_trusted_success(state: PageQualityState) -> bool:
    return state in _BLOCKING_STATES


def page_quality_configuration_hash() -> str:
    """Deterministic config identity for cache keys (no timestamps)."""
    return "page-quality-default-v1"


def apply_post_parse_quality_gate(
    document: CanonicalDocument,
    *,
    page_image_counts: dict[int, int] | None = None,
) -> PostParseGateResult:
    """Evaluate every page and downgrade trusted SUCCESS when required.

    Parsers must not publish final trusted status; this gate owns it.
    """
    image_counts = page_image_counts or {}
    page_states: list[PageQualityState] = []
    issues: list[PageQualityIssue] = []
    warnings = list(document.warnings)

    for page in document.pages:
        state, signals = diagnose_canonical_page(
            page,
            image_count=image_counts.get(page.page_number, 0),
            parser_status=document.status,
        )
        page_states.append(state)
        if blocks_trusted_success(state):
            issues.append(
                PageQualityIssue(
                    page_number=page.page_number,
                    state=state,
                    reason_code=state.value,
                    char_count=signals.char_count,
                    image_count=signals.image_count,
                )
            )
            warnings.append(
                ParseWarning(
                    code=ParseWarningCode.QUALITY_SIGNAL,
                    message=f"{state.value}: page lacks trusted complete text extract",
                    page_number=page.page_number,
                )
            )

    summary = PageQualitySummary(page_states=page_states, issues=issues)
    status = document.status
    if any(blocks_trusted_success(s) for s in page_states):
        if status is ParseStatus.SUCCESS:
            status = ParseStatus.PARTIAL
        if page_states and not any(page.raw_text.strip() for page in document.pages):
            status = ParseStatus.FAILED

    if status is document.status and warnings == list(document.warnings):
        return PostParseGateResult(document=document, summary=summary)

    metrics = compute_metrics(list(document.pages)) if document.pages else document.metrics
    updated = document.model_copy(
        update={
            "status": status,
            "warnings": warnings,
            "metrics": metrics,
        }
    )
    return PostParseGateResult(document=updated, summary=summary)


def validate_cached_trust(
    document: CanonicalDocument,
    summary: PageQualitySummary | None,
) -> str | None:
    """Return rejection reason if cache entry must not be trusted, else None."""
    if summary is None:
        return "missing_page_quality_summary"
    if summary.gate_version != PAGE_QUALITY_GATE_VERSION:
        return "page_quality_gate_version_mismatch"
    if document.status is ParseStatus.SUCCESS and any(
        blocks_trusted_success(state) for state in summary.page_states
    ):
        return "success_with_blocking_page_quality"
    if len(summary.page_states) != len(document.pages):
        return "page_quality_length_mismatch"
    return None
