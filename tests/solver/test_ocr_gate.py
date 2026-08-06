"""OCR quality gate unit tests."""

from __future__ import annotations

from halyk_agent.domain.page_quality import (
    PageQualityState,
    diagnose_canonical_page,
    trusted_success_blocked,
)
from halyk_agent.domain.parsing import CanonicalPage, ParseStatus
from halyk_agent.solver.ocr.diagnostics import probe_ocr_backend


def test_image_dominant_and_heading_without_body() -> None:
    empty = CanonicalPage(page_number=1, raw_text="")
    state, signals = diagnose_canonical_page(empty, image_count=2)
    assert state is PageQualityState.OCR_REQUIRED
    assert signals.image_count == 2

    heading = CanonicalPage(page_number=1, raw_text="LIMIT CLAUSE\n")
    state2, _ = diagnose_canonical_page(heading, image_count=0)
    assert state2 in {
        PageQualityState.OCR_REQUIRED,
        PageQualityState.HEADING_WITHOUT_BODY,
        PageQualityState.LOW_TEXT,
    }


def test_ocr_required_blocks_trusted_success() -> None:
    assert trusted_success_blocked(ParseStatus.SUCCESS, [PageQualityState.OCR_REQUIRED]) is True
    assert trusted_success_blocked(ParseStatus.SUCCESS, [PageQualityState.TEXT_OK]) is False


def test_text_ok_not_ocr_required() -> None:
    page = CanonicalPage(
        page_number=1,
        raw_text=("This is a sufficiently long alphanumeric policy paragraph. " * 5),
    )
    state, _ = diagnose_canonical_page(page, image_count=0)
    assert state is PageQualityState.TEXT_OK


def test_unavailable_backend_is_explicit() -> None:
    probe = probe_ocr_backend()
    assert probe["available"] is False
    assert "reason" in probe
