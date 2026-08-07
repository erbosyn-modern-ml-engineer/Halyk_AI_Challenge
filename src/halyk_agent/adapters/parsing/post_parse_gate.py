"""Backend-independent post-parse quality gate (authoritative trust status)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from halyk_agent.domain.page_quality import (
    ImageVisibility,
    PageQualityState,
    PageVisualSignals,
    diagnose_canonical_page,
    is_blocking_page_quality,
)
from halyk_agent.domain.parsing import (
    CanonicalDocument,
    ParseStatus,
    ParseWarning,
    ParseWarningCode,
    compute_metrics,
)

# Bump: Stage 5A.2 requires visual metadata (KNOWN/UNKNOWN) in trust decisions.
PAGE_QUALITY_GATE_VERSION = "halyk.page_quality_gate.v2"
OCR_POLICY_NONE = "ocr_disabled"
OCR_BACKEND_NONE = "NONE"


class PageQualityIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    page_number: int = Field(ge=1)
    state: PageQualityState
    reason_code: str
    char_count: int
    image_count: int
    image_visibility: ImageVisibility = ImageVisibility.UNKNOWN


class PageQualitySummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    gate_version: str = PAGE_QUALITY_GATE_VERSION
    page_states: list[PageQualityState]
    issues: list[PageQualityIssue] = Field(default_factory=list)
    page_visuals: list[PageVisualSignals] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class PostParseGateResult:
    document: CanonicalDocument
    summary: PageQualitySummary


def page_quality_configuration_hash() -> str:
    """Deterministic config identity for cache keys (no timestamps)."""
    return "page-quality-visual-v2"


def _visual_map(
    page_visuals: Sequence[PageVisualSignals] | None,
    page_numbers: Sequence[int],
) -> dict[int, PageVisualSignals]:
    if page_visuals:
        return {item.page_number: item for item in page_visuals}
    # Missing visuals → UNKNOWN (never silently zero-known).
    return {
        n: PageVisualSignals(
            page_number=n,
            image_count=0,
            image_visibility=ImageVisibility.UNKNOWN,
            extraction_warnings=["visual_metadata_not_provided"],
        )
        for n in page_numbers
    }


def apply_post_parse_quality_gate(
    document: CanonicalDocument,
    *,
    page_visuals: Sequence[PageVisualSignals] | None = None,
    page_image_counts: dict[int, int] | None = None,
) -> PostParseGateResult:
    """Evaluate every page and set authoritative trust status.

    Parsers must not publish final trusted status; this gate owns it.
    Idempotent: re-running on an already-gated document does not duplicate issues
    when warnings already carry the same page QUALITY_SIGNAL codes.
    """
    visuals = _visual_map(
        page_visuals,
        [page.page_number for page in document.pages],
    )
    # Backward-compat shim: explicit known counts override UNKNOWN placeholders.
    if page_image_counts:
        for page_no, count in page_image_counts.items():
            visuals[page_no] = PageVisualSignals(
                page_number=page_no,
                image_count=count,
                image_visibility=ImageVisibility.KNOWN,
            )

    page_states: list[PageQualityState] = []
    issues: list[PageQualityIssue] = []
    existing_quality_pages = {
        w.page_number
        for w in document.warnings
        if w.code is ParseWarningCode.QUALITY_SIGNAL and w.page_number is not None
    }
    warnings = list(document.warnings)
    ordered_visuals: list[PageVisualSignals] = []

    for page in document.pages:
        visual = visuals.get(
            page.page_number,
            PageVisualSignals(
                page_number=page.page_number,
                image_visibility=ImageVisibility.UNKNOWN,
            ),
        )
        ordered_visuals.append(visual)
        state, signals = diagnose_canonical_page(
            page,
            visual=visual,
            parser_status=document.status,
        )
        page_states.append(state)
        if is_blocking_page_quality(state):
            issues.append(
                PageQualityIssue(
                    page_number=page.page_number,
                    state=state,
                    reason_code=state.value,
                    char_count=signals.char_count,
                    image_count=signals.image_count,
                    image_visibility=signals.image_visibility,
                )
            )
            if page.page_number not in existing_quality_pages:
                warnings.append(
                    ParseWarning(
                        code=ParseWarningCode.QUALITY_SIGNAL,
                        message=f"{state.value}: page lacks trusted complete text extract",
                        page_number=page.page_number,
                    )
                )

    summary = PageQualitySummary(
        page_states=page_states,
        issues=issues,
        page_visuals=ordered_visuals,
    )

    # Trusted usable pages: non-blocking page-quality AND usable extracted text.
    # Text on a blocking page never makes that page trusted.
    trusted_usable_pages = [
        page
        for page, state in zip(document.pages, page_states, strict=False)
        if page.raw_text.strip() and not is_blocking_page_quality(state)
    ]
    has_blocking = any(is_blocking_page_quality(s) for s in page_states)

    status = document.status
    if status in {
        ParseStatus.SUCCESS,
        ParseStatus.PARTIAL,
        ParseStatus.FAILED,
        ParseStatus.QUALITY_REJECTED,
    }:
        if not trusted_usable_pages:
            # C/D: every page blocking, or no trusted usable content → FAILED
            status = ParseStatus.FAILED
        elif has_blocking:
            # B: at least one trusted usable page + at least one blocking page
            status = ParseStatus.PARTIAL
        else:
            # A: no blocking pages and trusted usable content
            status = ParseStatus.SUCCESS

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
        is_blocking_page_quality(state) for state in summary.page_states
    ):
        return "success_with_blocking_page_quality"
    if len(summary.page_states) != len(document.pages):
        return "page_quality_length_mismatch"
    return None
