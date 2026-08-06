"""OCR quality gate unit tests (domain + post-parse)."""

from __future__ import annotations

from halyk_agent.adapters.parsing.post_parse_gate import apply_post_parse_quality_gate
from halyk_agent.domain.page_quality import (
    ImageVisibility,
    PageQualityState,
    PageVisualSignals,
    diagnose_canonical_page,
    is_blocking_page_quality,
)
from halyk_agent.domain.parsing import (
    CanonicalDocument,
    CanonicalPage,
    ParserIdentity,
    ParserKind,
    ParseStatus,
    compute_metrics,
    document_identity,
)
from halyk_agent.solver.ocr.diagnostics import probe_ocr_backend


def _minimal_doc(pages: list[CanonicalPage]) -> CanonicalDocument:
    sha = "b" * 64
    return CanonicalDocument(
        id=document_identity("art", sha),
        artifact_id="art",
        document_id=document_identity("art", sha),
        document_version_id="dv",
        source_file="x.pdf",
        source_sha256=sha,
        parser=ParserIdentity(
            kind=ParserKind.PYPDF,
            package_name="pypdf",
            package_version="0",
            configuration_hash="c",
        ),
        status=ParseStatus.SUCCESS,
        pages=pages,
        metrics=compute_metrics(pages),
    )


def test_image_dominant_and_heading_without_body() -> None:
    empty = CanonicalPage(page_number=1, raw_text="")
    state, signals = diagnose_canonical_page(
        empty,
        visual=PageVisualSignals(
            page_number=1, image_count=2, image_visibility=ImageVisibility.KNOWN
        ),
    )
    assert state is PageQualityState.OCR_REQUIRED
    assert signals.image_count == 2

    heading = CanonicalPage(page_number=1, raw_text="LIMIT CLAUSE\n")
    state2, _ = diagnose_canonical_page(
        heading,
        visual=PageVisualSignals(
            page_number=1, image_count=0, image_visibility=ImageVisibility.KNOWN
        ),
    )
    assert state2 in {
        PageQualityState.OCR_REQUIRED,
        PageQualityState.HEADING_WITHOUT_BODY,
        PageQualityState.LOW_TEXT,
    }


def test_blocking_predicate_gates_success() -> None:
    assert is_blocking_page_quality(PageQualityState.OCR_REQUIRED) is True
    assert is_blocking_page_quality(PageQualityState.TEXT_OK) is False
    gated = apply_post_parse_quality_gate(
        _minimal_doc([CanonicalPage(page_number=1, raw_text="")]),
        page_visuals=[
            PageVisualSignals(page_number=1, image_count=1, image_visibility=ImageVisibility.KNOWN)
        ],
    )
    assert gated.document.status is not ParseStatus.SUCCESS


def test_text_ok_not_ocr_required() -> None:
    page = CanonicalPage(
        page_number=1,
        raw_text=("This is a sufficiently long alphanumeric policy paragraph. " * 5),
    )
    state, _ = diagnose_canonical_page(
        page,
        visual=PageVisualSignals(
            page_number=1, image_count=0, image_visibility=ImageVisibility.KNOWN
        ),
    )
    assert state is PageQualityState.TEXT_OK


def test_unavailable_backend_is_explicit() -> None:
    probe = probe_ocr_backend()
    assert probe["available"] is False
    assert "reason" in probe
