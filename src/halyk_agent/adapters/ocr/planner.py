"""Deterministic selective OCR planner over authoritative page-quality results."""

from __future__ import annotations

from collections.abc import Iterable

from halyk_agent.adapters.parsing.post_parse_gate import (
    PageQualitySummary,
    apply_post_parse_quality_gate,
)
from halyk_agent.domain.ocr import (
    DEFAULT_MAX_SELECTED_PAGES,
    OcrPageSelection,
    OcrPlan,
)
from halyk_agent.domain.page_quality import PageQualityState, is_blocking_page_quality
from halyk_agent.domain.parsing import CanonicalDocument


def _eligible(state: PageQualityState, *, visual_suggests_ocr: bool) -> bool:
    if not is_blocking_page_quality(state):
        return False
    if state is PageQualityState.UNREADABLE:
        return visual_suggests_ocr
    return state in {
        PageQualityState.OCR_REQUIRED,
        PageQualityState.IMAGE_DOMINANT,
        PageQualityState.HEADING_WITHOUT_BODY,
        PageQualityState.OCR_FAILED,
    }


def plan_selective_ocr(
    documents: Iterable[CanonicalDocument],
    *,
    source_paths: dict[str, str] | None = None,
    only_required: bool = True,
    max_pages: int = DEFAULT_MAX_SELECTED_PAGES,
    override_all_blocking: bool = False,
    total_pdfs: int = 0,
    total_pages: int = 0,
) -> OcrPlan:
    """Select blocking pages for OCR. Never falls back to whole-document OCR."""
    if max_pages < 1:
        raise ValueError("max_pages must be >= 1")
    if not only_required and not override_all_blocking:
        raise ValueError("competition default requires only_required=True")

    selections: list[OcrPageSelection] = []
    seen: set[tuple[str, int]] = set()
    blocking_count = 0
    paths = source_paths or {}

    for document in documents:
        gated = apply_post_parse_quality_gate(document)
        summary: PageQualitySummary = gated.summary
        visual_by_page = {v.page_number: v for v in summary.page_visuals}
        for page, state in zip(document.pages, summary.page_states, strict=False):
            if is_blocking_page_quality(state):
                blocking_count += 1
            visual = visual_by_page.get(page.page_number)
            suggests = bool(
                visual is not None
                and (visual.image_count >= 1 or visual.image_visibility.value == "UNKNOWN")
            )
            if only_required and not _eligible(state, visual_suggests_ocr=suggests):
                continue
            if not only_required and override_all_blocking and not is_blocking_page_quality(state):
                continue
            key = (document.source_sha256, page.page_number)
            if key in seen:
                continue
            seen.add(key)
            source_path = paths.get(document.artifact_id) or paths.get(document.id)
            if not source_path:
                source_path = document.source_file
            selections.append(
                OcrPageSelection(
                    source_path=source_path,
                    source_sha256=document.source_sha256,
                    document_id=document.document_id,
                    document_version_id=document.document_version_id,
                    artifact_id=document.artifact_id,
                    page_number=page.page_number,
                    page_quality_state=state,
                    reason=f"blocking:{state.value}",
                )
            )

    selections.sort(key=lambda item: (item.source_sha256, item.page_number, item.artifact_id))
    truncated = selections[:max_pages]
    return OcrPlan(
        only_required=only_required,
        max_pages=max_pages,
        override_active=bool(override_all_blocking and not only_required),
        selections=truncated,
        total_pdfs=total_pdfs,
        total_pages=total_pages,
        blocking_pages=blocking_count,
    )
