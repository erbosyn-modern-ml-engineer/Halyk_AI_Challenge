"""H2: backend-independent post-parse quality gate."""

from __future__ import annotations

from halyk_agent.adapters.archive.hashing import sha256_bytes
from halyk_agent.adapters.parsing.post_parse_gate import apply_post_parse_quality_gate
from halyk_agent.adapters.parsing.pypdf_parser import PyPdfDocumentParser
from halyk_agent.domain.evidence_factory import build_evidence_catalog
from halyk_agent.domain.page_quality import (
    ImageVisibility,
    PageQualityState,
    PageVisualSignals,
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
from tests.parsing.helpers import make_text_pdf


def _parser(backend: str) -> ParserIdentity:
    kind = ParserKind.PYPDF if backend == "pypdf" else ParserKind.DOCLING
    return ParserIdentity(
        kind=kind,
        package_name=backend,
        package_version="0.0.0",
        configuration_hash="cfg",
    )


def _doc(
    pages: list[CanonicalPage],
    *,
    status: ParseStatus = ParseStatus.SUCCESS,
    backend: str = "pypdf",
) -> CanonicalDocument:
    sha = "a" * 64
    return CanonicalDocument(
        id=document_identity("art1", sha),
        artifact_id="art1",
        document_id=document_identity("art1", sha),
        document_version_id="dv1",
        source_file="x.pdf",
        source_sha256=sha,
        mime_type="application/pdf",
        parser=_parser(backend),
        status=status,
        pages=pages,
        metrics=compute_metrics(pages),
        warnings=[],
    )


def test_pypdf_and_docling_identical_page_quality() -> None:
    pages = [CanonicalPage(page_number=1, raw_text="LIMIT CLAUSE\n")]
    visuals = [
        PageVisualSignals(page_number=1, image_count=0, image_visibility=ImageVisibility.KNOWN)
    ]
    pypdf_result = apply_post_parse_quality_gate(_doc(pages, backend="pypdf"), page_visuals=visuals)
    docling_result = apply_post_parse_quality_gate(
        _doc(pages, backend="docling"), page_visuals=visuals
    )
    assert pypdf_result.summary.page_states == docling_result.summary.page_states
    assert pypdf_result.document.status == docling_result.document.status
    assert pypdf_result.document.status is not ParseStatus.SUCCESS


def test_docling_heading_without_body_not_trusted_success() -> None:
    pages = [CanonicalPage(page_number=1, raw_text="HEADING ONLY")]
    result = apply_post_parse_quality_gate(
        _doc(pages, backend="docling"),
        page_visuals=[
            PageVisualSignals(page_number=1, image_count=0, image_visibility=ImageVisibility.KNOWN)
        ],
    )
    assert result.document.status is not ParseStatus.SUCCESS
    assert any(issue.state is PageQualityState.OCR_REQUIRED for issue in result.summary.issues)


def test_unknown_visuals_empty_page_not_success() -> None:
    pages = [CanonicalPage(page_number=1, raw_text="")]
    result = apply_post_parse_quality_gate(
        _doc(pages),
        page_visuals=[
            PageVisualSignals(
                page_number=1, image_count=0, image_visibility=ImageVisibility.UNKNOWN
            )
        ],
    )
    assert result.document.status is not ParseStatus.SUCCESS


def test_readable_pages_remain_usable_and_evidenced() -> None:
    data = make_text_pdf(
        [
            "Sufficient alphanumeric covenant body text for trusted extract on page one.",
            "",
        ]
    )
    candidate, visuals = PyPdfDocumentParser().parse_with_visuals(
        data,
        source_file="mixed.pdf",
        artifact_id="mixed",
        source_sha256=sha256_bytes(data),
    )
    # Force second page empty with known images.
    pages = list(candidate.pages)
    if len(pages) == 1:
        pages.append(CanonicalPage(page_number=2, raw_text=""))
        visuals = [
            *visuals,
            PageVisualSignals(page_number=2, image_count=2, image_visibility=ImageVisibility.KNOWN),
        ]
    else:
        pages[1] = pages[1].model_copy(update={"raw_text": "", "normalized_text": "", "blocks": []})
        visuals = [
            visuals[0],
            PageVisualSignals(page_number=2, image_count=2, image_visibility=ImageVisibility.KNOWN),
        ]
    doc = candidate.model_copy(
        update={"pages": pages, "metrics": compute_metrics(pages), "status": ParseStatus.SUCCESS}
    )
    result = apply_post_parse_quality_gate(doc, page_visuals=visuals)
    assert result.document.status is ParseStatus.PARTIAL
    assert result.summary.page_states[0] is PageQualityState.TEXT_OK
    assert result.summary.page_states[1] is PageQualityState.OCR_REQUIRED
    spans = build_evidence_catalog(result.document)
    assert spans
    assert all(span.page_number == 1 for span in spans)


def test_pypdf_result_passes_through_common_gate() -> None:
    data = make_text_pdf(["LIMIT"])
    candidate, visuals = PyPdfDocumentParser().parse_with_visuals(
        data,
        source_file="h.pdf",
        artifact_id="h",
        source_sha256=sha256_bytes(data),
    )
    gated = apply_post_parse_quality_gate(candidate, page_visuals=visuals)
    assert gated.document.status is not ParseStatus.SUCCESS
    assert gated.summary.gate_version.startswith("halyk.page_quality_gate.v2")


def test_heading_only_with_known_image_is_failed_no_evidence() -> None:
    pages = [CanonicalPage(page_number=1, raw_text="HEADING ONLY")]
    result = apply_post_parse_quality_gate(
        _doc(pages),
        page_visuals=[
            PageVisualSignals(page_number=1, image_count=2, image_visibility=ImageVisibility.KNOWN)
        ],
    )
    assert result.document.status is ParseStatus.FAILED
    assert all(is_blocking_page_quality(s) for s in result.summary.page_states)
    # No block geometry → no fabricated EvidenceSpan; FAILED stays out of app catalogue.
    assert build_evidence_catalog(result.document) == []


def test_all_blocking_pages_with_heading_text_are_failed() -> None:
    pages = [
        CanonicalPage(page_number=1, raw_text="LIMIT SUMMARY"),
        CanonicalPage(page_number=2, raw_text="SCHEDULE A"),
    ]
    visuals = [
        PageVisualSignals(page_number=1, image_count=1, image_visibility=ImageVisibility.KNOWN),
        PageVisualSignals(page_number=2, image_count=1, image_visibility=ImageVisibility.KNOWN),
    ]
    result = apply_post_parse_quality_gate(_doc(pages), page_visuals=visuals)
    assert result.document.status is ParseStatus.FAILED
    assert len(result.summary.issues) == 2
    assert all(is_blocking_page_quality(s) for s in result.summary.page_states)
    assert build_evidence_catalog(result.document) == []


def test_mixed_trusted_and_blocking_is_partial_with_trusted_evidence() -> None:
    data = make_text_pdf(
        [
            "Sufficient alphanumeric covenant body text for trusted extract on page one.",
            "HEADING ONLY",
        ]
    )
    candidate, visuals = PyPdfDocumentParser().parse_with_visuals(
        data,
        source_file="mixed2.pdf",
        artifact_id="mixed2",
        source_sha256=sha256_bytes(data),
    )
    # Force page 2 into OCR_REQUIRED via known images + short heading text.
    # Clear blocks so evidence catalogue stays trusted-page-only (no fabricated spans).
    pages = list(candidate.pages)
    pages[1] = pages[1].model_copy(
        update={
            "raw_text": "HEADING ONLY",
            "normalized_text": "HEADING ONLY",
            "blocks": [],
            "tables": [],
        }
    )
    visuals = [
        PageVisualSignals(page_number=1, image_count=0, image_visibility=ImageVisibility.KNOWN),
        PageVisualSignals(page_number=2, image_count=3, image_visibility=ImageVisibility.KNOWN),
    ]
    doc = candidate.model_copy(
        update={"pages": pages, "metrics": compute_metrics(pages), "status": ParseStatus.SUCCESS}
    )
    result = apply_post_parse_quality_gate(doc, page_visuals=visuals)
    assert result.document.status is ParseStatus.PARTIAL
    assert result.summary.page_states[0] is PageQualityState.TEXT_OK
    assert is_blocking_page_quality(result.summary.page_states[1])
    spans = build_evidence_catalog(result.document)
    assert spans
    assert all(span.page_number == 1 for span in spans)


def test_normal_readable_document_is_success() -> None:
    data = make_text_pdf(
        [
            "Sufficient alphanumeric covenant body text for trusted extract "
            "on page one with clear obligations and numeric limits."
        ]
    )
    candidate, visuals = PyPdfDocumentParser().parse_with_visuals(
        data,
        source_file="ok.pdf",
        artifact_id="ok",
        source_sha256=sha256_bytes(data),
    )
    # Ensure verified zero-image text page (not UNKNOWN).
    visuals = [
        PageVisualSignals(page_number=1, image_count=0, image_visibility=ImageVisibility.KNOWN)
    ]
    result = apply_post_parse_quality_gate(candidate, page_visuals=visuals)
    assert result.document.status is ParseStatus.SUCCESS
    assert result.summary.page_states == [PageQualityState.TEXT_OK]
    assert build_evidence_catalog(result.document)
