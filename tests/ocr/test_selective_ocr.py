"""Stage 5A.4 selective provenance-safe OCR tests (mocked backend)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from halyk_agent.adapters.ocr.cache import LocalOcrPageCache
from halyk_agent.adapters.ocr.merge import merge_ocr_into_document
from halyk_agent.adapters.ocr.mock import MockOcrBackend
from halyk_agent.adapters.ocr.planner import plan_selective_ocr
from halyk_agent.adapters.ocr.probe import probe_ocr_environment, probe_tesseract_cli
from halyk_agent.adapters.parsing.post_parse_gate import apply_post_parse_quality_gate
from halyk_agent.app.ocr import SelectiveOcrError, run_ocr_probe, run_selective_ocr
from halyk_agent.domain.evidence_factory import build_evidence_catalog
from halyk_agent.domain.ocr import (
    OcrBackendIdentity,
    OcrBackendKind,
    OcrPageRequest,
    OcrPageResult,
    OcrPageStatus,
    OcrTextBlock,
    TextOrigin,
    ocr_cache_identity,
    ocr_configuration_hash,
    validate_ocr_page_text,
)
from halyk_agent.domain.page_quality import (
    ImageVisibility,
    PageQualityState,
    PageVisualSignals,
    is_blocking_page_quality,
)
from halyk_agent.domain.parsing import (
    CanonicalDocument,
    CanonicalPage,
    ParseBatchReport,
    ParseResult,
    ParserIdentity,
    ParserKind,
    ParseStatus,
    QualityDecision,
    compute_metrics,
    document_identity,
)


def _parser() -> ParserIdentity:
    return ParserIdentity(
        kind=ParserKind.PYPDF,
        package_name="pypdf",
        package_version="0",
        configuration_hash="cfg",
    )


def _doc(
    pages: list[CanonicalPage],
    *,
    artifact_id: str = "art",
    status: ParseStatus = ParseStatus.SUCCESS,
) -> CanonicalDocument:
    sha = "b" * 64
    return CanonicalDocument(
        id=document_identity(artifact_id, sha),
        artifact_id=artifact_id,
        document_id=document_identity(artifact_id, sha),
        document_version_id="dv1",
        source_file=f"{artifact_id}.pdf",
        source_sha256=sha,
        mime_type="application/pdf",
        parser=_parser(),
        status=status,
        pages=pages,
        metrics=compute_metrics(pages),
        warnings=[],
    )


def test_probe_never_downloads() -> None:
    report = probe_ocr_environment()
    assert report.downloads_performed is False
    tess = next(c for c in report.candidates if c.kind is OcrBackendKind.TESSERACT_CLI)
    assert "eng" in tess.required_languages
    assert "rus" in tess.required_languages
    assert "kaz" in tess.required_languages
    assert tess.may_download is False
    assert tess.network_required is False


def test_probe_tesseract_missing_executable_structure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "halyk_agent.adapters.ocr.probe.discover_tesseract_executable",
        lambda: None,
    )
    avail = probe_tesseract_cli()
    assert avail.installed is False
    assert avail.offline_ready is False
    assert "tesseract_executable" in avail.missing_components
    assert set(avail.missing_languages) >= {"eng", "rus", "kaz"}


def test_planner_selects_only_blocking_sorted_deduped() -> None:
    pages = [
        CanonicalPage(
            page_number=1, raw_text="Enough trusted alphanumeric covenant body text here."
        ),
        CanonicalPage(page_number=2, raw_text=""),
        CanonicalPage(page_number=2, raw_text=""),  # duplicate page number ignored by doc model?
    ]
    # CanonicalPage list with unique page numbers
    pages = [
        CanonicalPage(
            page_number=1, raw_text="Enough trusted alphanumeric covenant body text here."
        ),
        CanonicalPage(page_number=2, raw_text=""),
        CanonicalPage(page_number=3, raw_text="HEADING ONLY"),
    ]
    doc = _doc(pages)
    visuals = [
        PageVisualSignals(page_number=1, image_count=0, image_visibility=ImageVisibility.KNOWN),
        PageVisualSignals(page_number=2, image_count=2, image_visibility=ImageVisibility.KNOWN),
        PageVisualSignals(page_number=3, image_count=1, image_visibility=ImageVisibility.KNOWN),
    ]
    gated = apply_post_parse_quality_gate(doc, page_visuals=visuals)
    plan = plan_selective_ocr([gated.document], max_pages=32, total_pdfs=1, total_pages=3)
    assert plan.only_required is True
    nums = [s.page_number for s in plan.selections]
    assert nums == sorted(set(nums))
    assert 1 not in nums
    assert all(is_blocking_page_quality(s.page_quality_state) for s in plan.selections)


def test_planner_respects_max_pages_no_whole_document() -> None:
    pages = [CanonicalPage(page_number=i, raw_text="") for i in range(1, 6)]
    visuals = [
        PageVisualSignals(page_number=i, image_count=1, image_visibility=ImageVisibility.KNOWN)
        for i in range(1, 6)
    ]
    gated = apply_post_parse_quality_gate(_doc(pages), page_visuals=visuals)
    plan = plan_selective_ocr([gated.document], max_pages=2)
    assert len(plan.selections) == 2
    assert plan.blocking_pages == 5


def test_planner_deterministic() -> None:
    pages = [
        CanonicalPage(page_number=1, raw_text=""),
        CanonicalPage(page_number=2, raw_text=""),
    ]
    visuals = [
        PageVisualSignals(page_number=1, image_count=1, image_visibility=ImageVisibility.KNOWN),
        PageVisualSignals(page_number=2, image_count=1, image_visibility=ImageVisibility.KNOWN),
    ]
    gated = apply_post_parse_quality_gate(_doc(pages), page_visuals=visuals)
    a = plan_selective_ocr([gated.document], max_pages=10)
    b = plan_selective_ocr([gated.document], max_pages=10)
    assert a.model_dump() == b.model_dump()


@pytest.mark.asyncio
async def test_backend_preserves_order_and_isolates_failures() -> None:
    backend = MockOcrBackend(fail_pages={2})
    reqs = [
        OcrPageRequest(
            source_path="a.pdf",
            source_sha256="a" * 64,
            document_id="d",
            document_version_id="v",
            page_number=page,
            reason="blocking:OCR_REQUIRED",
            page_quality_state=PageQualityState.OCR_REQUIRED,
            languages=["eng", "rus", "kaz"],
        )
        for page in (1, 2, 3)
    ]
    results = await backend.recognize_pages(reqs)
    assert [r.request.page_number for r in results] == [1, 2, 3]
    assert results[0].status is OcrPageStatus.OCR_SUCCEEDED
    assert results[1].status is OcrPageStatus.OCR_FAILED
    assert results[2].status is OcrPageStatus.OCR_SUCCEEDED
    assert backend.invoked_pages == [1, 2, 3]


@pytest.mark.asyncio
async def test_unselected_pages_never_reach_backend() -> None:
    backend = MockOcrBackend()
    pages = [
        CanonicalPage(
            page_number=1, raw_text="Enough trusted alphanumeric covenant body text here."
        ),
        CanonicalPage(page_number=2, raw_text=""),
    ]
    visuals = [
        PageVisualSignals(page_number=1, image_count=0, image_visibility=ImageVisibility.KNOWN),
        PageVisualSignals(page_number=2, image_count=3, image_visibility=ImageVisibility.KNOWN),
    ]
    gated = apply_post_parse_quality_gate(_doc(pages), page_visuals=visuals)
    plan = plan_selective_ocr([gated.document])
    reqs = [
        OcrPageRequest(
            source_path=s.source_path,
            source_sha256=s.source_sha256,
            document_id=s.document_id,
            document_version_id=s.document_version_id,
            page_number=s.page_number,
            reason=s.reason,
            page_quality_state=s.page_quality_state,
            languages=["eng", "rus", "kaz"],
        )
        for s in plan.selections
    ]
    await backend.recognize_pages(reqs)
    assert backend.invoked_pages == [2]
    assert 1 not in backend.invoked_pages


def test_merge_preserves_embedded_and_sets_ocr_origin() -> None:
    page = CanonicalPage(page_number=1, raw_text="Embedded alphanumeric body text remains.")
    doc = _doc([page])
    identity = MockOcrBackend().identity()
    req = OcrPageRequest(
        source_path="x.pdf",
        source_sha256=doc.source_sha256,
        document_id=doc.document_id,
        document_version_id=doc.document_version_id,
        page_number=1,
        reason="blocking:OCR_REQUIRED",
        page_quality_state=PageQualityState.OCR_REQUIRED,
        languages=["eng", "rus", "kaz"],
    )
    ocr_text = (
        "Recovered alphanumeric covenant body text from OCR with enough characters "
        "for trusted quality validation on this page."
    )
    result = OcrPageResult(
        request=req,
        status=OcrPageStatus.OCR_SUCCEEDED,
        blocks=[
            OcrTextBlock(
                text=ocr_text,
                page_number=1,
                bbox=(1.0, 1.0, 100.0, 20.0),
                reading_order=0,
                confidence=0.9,
                origin=TextOrigin.OCR,
                backend=identity,
                source_image_identity="img1",
            )
        ],
    )
    merged, _remaining = merge_ocr_into_document(doc, [result])
    assert "Embedded alphanumeric body text remains." in merged.pages[0].raw_text
    assert ocr_text in merged.pages[0].raw_text
    origins = [b.metadata.get("text_origin") for b in merged.pages[0].blocks]
    assert TextOrigin.EMBEDDED_PDF_TEXT.value in origins
    assert TextOrigin.OCR.value in origins
    spans = build_evidence_catalog(merged)
    ocr_spans = [s for s in spans if s.text_origin is TextOrigin.OCR]
    assert ocr_spans
    for span in ocr_spans:
        assert span.quote in merged.pages[0].raw_text
        assert span.ocr_backend_identity


def test_invalid_ocr_rejected_and_no_synthetic_evidence() -> None:
    assert validate_ocr_page_text("") is OcrPageStatus.OCR_FAILED
    assert validate_ocr_page_text("!!!!") is OcrPageStatus.OCR_LOW_QUALITY
    page = CanonicalPage(page_number=1, raw_text="")
    doc = _doc([page])
    req = OcrPageRequest(
        source_path="x.pdf",
        source_sha256=doc.source_sha256,
        document_id=doc.document_id,
        document_version_id=doc.document_version_id,
        page_number=1,
        reason="blocking:OCR_REQUIRED",
        page_quality_state=PageQualityState.OCR_REQUIRED,
        languages=["eng", "rus", "kaz"],
    )
    result = OcrPageResult(
        request=req,
        status=OcrPageStatus.OCR_FAILED,
        failure_reason=None,
        message="OCR failed",
        blocks=[],
    )
    merged, remaining = merge_ocr_into_document(doc, [result])
    assert "OCR failed" not in merged.pages[0].raw_text
    assert remaining >= 1


def test_full_recovery_may_success_partial_when_mixed() -> None:
    pages = [
        CanonicalPage(page_number=1, raw_text=""),
        CanonicalPage(page_number=2, raw_text=""),
    ]
    visuals = [
        PageVisualSignals(page_number=1, image_count=2, image_visibility=ImageVisibility.KNOWN),
        PageVisualSignals(page_number=2, image_count=2, image_visibility=ImageVisibility.KNOWN),
    ]
    gated = apply_post_parse_quality_gate(_doc(pages), page_visuals=visuals)
    assert gated.document.status is ParseStatus.FAILED
    identity = MockOcrBackend().identity()
    good = (
        "Recovered alphanumeric covenant body text for page one with sufficient "
        "characters for trusted OCR quality validation."
    )

    def _req(page: int) -> OcrPageRequest:
        return OcrPageRequest(
            source_path="x.pdf",
            source_sha256=gated.document.source_sha256,
            document_id=gated.document.document_id,
            document_version_id=gated.document.document_version_id,
            page_number=page,
            reason="blocking:OCR_REQUIRED",
            page_quality_state=PageQualityState.OCR_REQUIRED,
            languages=["eng", "rus", "kaz"],
        )

    full = [
        OcrPageResult(
            request=_req(1),
            status=OcrPageStatus.OCR_SUCCEEDED,
            blocks=[
                OcrTextBlock(
                    text=good,
                    page_number=1,
                    reading_order=0,
                    confidence=0.9,
                    origin=TextOrigin.OCR,
                    backend=identity,
                    source_image_identity="i1",
                )
            ],
        ),
        OcrPageResult(
            request=_req(2),
            status=OcrPageStatus.OCR_SUCCEEDED,
            blocks=[
                OcrTextBlock(
                    text=good.replace("one", "two"),
                    page_number=2,
                    reading_order=0,
                    confidence=0.9,
                    origin=TextOrigin.OCR,
                    backend=identity,
                    source_image_identity="i2",
                )
            ],
        ),
    ]
    merged_full, remaining_full = merge_ocr_into_document(gated.document, full)
    assert remaining_full == 0
    assert merged_full.status is ParseStatus.SUCCESS

    mixed = [
        full[0],
        OcrPageResult(
            request=_req(2),
            status=OcrPageStatus.OCR_FAILED,
            message="backend unavailable",
            blocks=[],
        ),
    ]
    merged_mixed, remaining_mixed = merge_ocr_into_document(gated.document, mixed)
    assert remaining_mixed >= 1
    assert merged_mixed.status is ParseStatus.PARTIAL


def test_cache_identity_changes_with_backend() -> None:
    a = OcrBackendIdentity(
        kind=OcrBackendKind.MOCK,
        backend_version="1",
        executable_or_package="mock",
        language_data_identity="ld",
        languages=["eng", "rus", "kaz"],
        render_scale=2.0,
        page_segmentation_mode=6,
        configuration_hash=ocr_configuration_hash(
            languages=["eng", "rus", "kaz"], render_scale=2.0, page_segmentation_mode=6
        ),
    )
    b = a.model_copy(update={"backend_version": "2", "configuration_hash": "other"})
    assert ocr_cache_identity(
        source_sha256="a" * 64, page_number=1, backend=a
    ) != ocr_cache_identity(source_sha256="a" * 64, page_number=1, backend=b)


def test_ocr_cache_roundtrip(tmp_path: Path) -> None:
    backend = MockOcrBackend()
    identity = backend.identity()
    req = OcrPageRequest(
        source_path="x.pdf",
        source_sha256="c" * 64,
        document_id="d",
        document_version_id="v",
        page_number=1,
        reason="blocking:OCR_REQUIRED",
        page_quality_state=PageQualityState.OCR_REQUIRED,
        languages=["eng", "rus", "kaz"],
    )
    result = OcrPageResult(
        request=req,
        status=OcrPageStatus.OCR_SUCCEEDED,
        blocks=[
            OcrTextBlock(
                text="Recovered alphanumeric covenant body text for cache roundtrip validation.",
                page_number=1,
                reading_order=0,
                origin=TextOrigin.OCR,
                backend=identity,
                source_image_identity="img",
            )
        ],
    )
    cache = LocalOcrPageCache(tmp_path / "cache")
    cache.put(result, backend=identity)
    hit = cache.get(source_sha256=req.source_sha256, page_number=1, backend=identity)
    assert hit is not None
    assert hit.status is OcrPageStatus.OCR_SUCCEEDED


@pytest.mark.asyncio
async def test_app_run_with_mock_and_overwrite(tmp_path: Path) -> None:
    pages = [CanonicalPage(page_number=1, raw_text="")]
    visuals = [
        PageVisualSignals(page_number=1, image_count=2, image_visibility=ImageVisibility.KNOWN)
    ]
    gated = apply_post_parse_quality_gate(_doc(pages, artifact_id="scan"), page_visuals=visuals)
    parsed = tmp_path / "parsed"
    (parsed / "documents").mkdir(parents=True)
    # FAILED docs are not written by parse app; put selected into report only.
    report = ParseBatchReport(
        profile="fast",
        total_candidates=1,
        successful=0,
        partial=0,
        failed=1,
        unsupported=0,
        cache_hits=0,
        results=[
            ParseResult(
                artifact_id="scan",
                selected_document=gated.document,
                attempts=[],
                quality_decision=QualityDecision.HUMAN_REVIEW_REQUIRED,
                cache_hit=False,
            )
        ],
    )
    (parsed / "parse_report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    (parsed / "evidence_catalog.jsonl").write_text("", encoding="utf-8")
    out = tmp_path / "ocr-out"
    out.mkdir()
    (out / "marker").write_text("x", encoding="utf-8")
    with pytest.raises(SelectiveOcrError, match="overwrite"):
        await run_selective_ocr(parsed, out, backend=MockOcrBackend())
    run = await run_selective_ocr(
        parsed,
        out,
        overwrite=True,
        backend=MockOcrBackend(),
        only_required=True,
        max_pages=32,
    )
    assert run.selected_pages == 1
    assert run.attempted_pages == 1
    assert run.succeeded_pages == 1
    assert (out / "ocr_report.json").is_file()
    json.loads((out / "ocr_probe.json").read_text(encoding="utf-8"))
    json.loads((out / "ocr_plan.json").read_text(encoding="utf-8"))
    reloaded = json.loads((out / "ocr_report.json").read_text(encoding="utf-8"))
    assert reloaded["selected_pages"] == 1


def test_cli_probe_output_validates() -> None:
    report, text = run_ocr_probe(json_output=True)
    assert report.schema_version.startswith("halyk.ocr_probe")
    payload = json.loads(text)
    assert "candidates" in payload
    assert payload["downloads_performed"] is False


@pytest.mark.asyncio
async def test_unavailable_backend_exits_service_error(tmp_path: Path) -> None:
    pages = [CanonicalPage(page_number=1, raw_text="")]
    visuals = [
        PageVisualSignals(page_number=1, image_count=1, image_visibility=ImageVisibility.KNOWN)
    ]
    gated = apply_post_parse_quality_gate(_doc(pages), page_visuals=visuals)
    parsed = tmp_path / "parsed"
    parsed.mkdir()
    report = ParseBatchReport(
        profile="fast",
        total_candidates=1,
        successful=0,
        partial=0,
        failed=1,
        unsupported=0,
        cache_hits=0,
        results=[
            ParseResult(
                artifact_id="art",
                selected_document=gated.document,
                attempts=[],
                quality_decision=QualityDecision.HUMAN_REVIEW_REQUIRED,
            )
        ],
    )
    (parsed / "parse_report.json").write_text(report.model_dump_json(), encoding="utf-8")
    out = tmp_path / "out"
    with pytest.raises(SelectiveOcrError, match="backend"):
        await run_selective_ocr(parsed, out, backend=MockOcrBackend(offline_ready=False))
