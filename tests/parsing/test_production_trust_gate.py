"""Stage 5A.2 production trust-gate regressions (H2)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from halyk_agent.adapters.archive.hashing import sha256_bytes
from halyk_agent.adapters.parsing.cache import CacheGetStatus, LocalParseCache
from halyk_agent.adapters.parsing.docling_mapping import extract_docling_page_visuals
from halyk_agent.adapters.parsing.docling_parser import DoclingDocumentParser
from halyk_agent.adapters.parsing.errors import ParseCacheError
from halyk_agent.adapters.parsing.finalize import to_authoritative_parse_result
from halyk_agent.adapters.parsing.limits import ParserLimits
from halyk_agent.adapters.parsing.post_parse_gate import (
    PAGE_QUALITY_GATE_VERSION,
    apply_post_parse_quality_gate,
)
from halyk_agent.adapters.parsing.pypdf_parser import PyPdfDocumentParser
from halyk_agent.contracts.parsing import ParseRequest
from halyk_agent.domain.datasets import ArtifactFormat
from halyk_agent.domain.page_quality import (
    ImageVisibility,
    PageQualityState,
    PageVisualSignals,
    is_blocking_page_quality,
)
from halyk_agent.domain.parsing import CanonicalPage, ParseStatus
from tests.ingestion.helpers import write_zip
from tests.parsing.helpers import make_empty_page_pdf
from tests.parsing.test_post_parse_gate import _doc

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_SCANNED = ROOT / "agentic-bank-public" / "documents" / "f3fa6d20c8a1.pdf"


@pytest.mark.asyncio
async def test_direct_pypdf_parse_gates_empty_pages(tmp_path: Path) -> None:
    data = make_empty_page_pdf()
    source = tmp_path / "blank.pdf"
    source.write_bytes(data)
    parser = PyPdfDocumentParser()
    result = await parser.parse(
        ParseRequest(
            artifact_id="blank",
            source_file="blank.pdf",
            source_path=source,
            source_sha256=sha256_bytes(data),
            format=ArtifactFormat.PDF,
            mime_type="application/pdf",
        )
    )
    assert result.selected_document is not None
    assert result.selected_document.status is not ParseStatus.SUCCESS


@pytest.mark.asyncio
async def test_direct_pypdf_parse_blocks_public_scanned_pdf() -> None:
    if not PUBLIC_SCANNED.is_file():
        pytest.skip("public training dataset absent — f3fa6d20c8a1.pdf not found")
    data = PUBLIC_SCANNED.read_bytes()
    parser = PyPdfDocumentParser()
    result = await parser.parse(
        ParseRequest(
            artifact_id="scan",
            source_file=PUBLIC_SCANNED.name,
            source_path=PUBLIC_SCANNED,
            source_sha256=sha256_bytes(data),
            format=ArtifactFormat.PDF,
            mime_type="application/pdf",
        )
    )
    doc = result.selected_document
    assert doc is not None
    assert doc.status is not ParseStatus.SUCCESS
    assert len(doc.pages) == 3
    _candidate, visuals = parser.parse_with_visuals(
        data,
        source_file=PUBLIC_SCANNED.name,
        artifact_id="scan",
        source_sha256=sha256_bytes(data),
    )
    assert all(v.image_visibility is ImageVisibility.KNOWN for v in visuals)
    assert all(v.image_count >= 1 for v in visuals)
    gated = apply_post_parse_quality_gate(_candidate, page_visuals=visuals)
    assert {issue.page_number for issue in gated.summary.issues} == {1, 2, 3}
    assert all(is_blocking_page_quality(s) for s in gated.summary.page_states)


def test_application_parse_path_blocks_public_scanned(tmp_path: Path) -> None:
    if not PUBLIC_SCANNED.is_file():
        pytest.skip("public training dataset absent — f3fa6d20c8a1.pdf not found")

    from halyk_agent.app.parsing import parse_inspection_directory
    from halyk_agent.config import Settings

    archive = tmp_path / "scan.zip"
    write_zip(archive, {PUBLIC_SCANNED.name: PUBLIC_SCANNED.read_bytes()})
    inspection = tmp_path / "inspection"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "halyk_agent",
            "inspect",
            "--input",
            str(archive),
            "--output",
            str(inspection),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    out = tmp_path / "parsed"
    report = parse_inspection_directory(
        inspection,
        out,
        profile="fast",
        settings=Settings(_env_file=None, stage=5),
    )
    assert report.total_candidates >= 1
    assert report.failed >= 1
    assert report.successful == 0
    report_payload = json.loads((out / "parse_report.json").read_text(encoding="utf-8"))
    selected = report_payload["results"][0]["selected_document"]
    assert selected is not None
    assert selected["status"] != ParseStatus.SUCCESS.value
    assert len(selected["pages"]) == 3
    # FAILED docs are not published under documents/; evidence must stay empty.
    assert list((out / "documents").glob("*.json")) == []
    evidence = (out / "evidence_catalog.jsonl").read_text(encoding="utf-8").strip()
    assert evidence == ""
    # Image metadata reached the gate (not silently all-zero).
    cand, visuals = PyPdfDocumentParser().parse_with_visuals(
        PUBLIC_SCANNED.read_bytes(),
        source_file=PUBLIC_SCANNED.name,
        artifact_id="scan",
        source_sha256=sha256_bytes(PUBLIC_SCANNED.read_bytes()),
    )
    assert all(v.image_count >= 1 for v in visuals)
    gated = apply_post_parse_quality_gate(cand, page_visuals=visuals)
    assert {issue.page_number for issue in gated.summary.issues} == {1, 2, 3}


def test_docling_parse_uses_picture_visuals() -> None:
    parser = DoclingDocumentParser(limits=ParserLimits(), ocr_enabled=False)

    class _FakeConverter:
        def convert(self, _path: str) -> SimpleNamespace:
            picture = SimpleNamespace(prov=[SimpleNamespace(page_no=1)])
            doc = SimpleNamespace(
                pictures=[picture],
                pages={1: SimpleNamespace(size=SimpleNamespace(width=100, height=100))},
                texts=[],
                tables=[],
            )
            return SimpleNamespace(document=doc)

    # Bypass ensure + mapping via monkeypatch on instance method internals.
    import halyk_agent.adapters.parsing.docling_parser as dp_mod

    original_ensure = dp_mod.ensure_docling_available
    dp_mod.ensure_docling_available = lambda: None
    try:
        import halyk_agent.adapters.parsing.docling_mapping as map_mod

        original_map = map_mod.map_docling_document
        map_mod.map_docling_document = lambda _doc, document_id: (  # type: ignore[assignment]
            [CanonicalPage(page_number=1, raw_text="", width=100, height=100)],
            [],
        )
        try:
            data = b"%PDF-1.4 fake"
            candidate, visuals = parser.parse_with_visuals(
                data,
                source_file="pic.pdf",
                artifact_id="pic",
                source_sha256=sha256_bytes(data),
                converter=_FakeConverter(),
            )
        finally:
            map_mod.map_docling_document = original_map
    finally:
        dp_mod.ensure_docling_available = original_ensure

    assert visuals[0].image_visibility is ImageVisibility.KNOWN
    assert visuals[0].image_count == 1
    result, _ = to_authoritative_parse_result(candidate, page_visuals=visuals)
    assert result.selected_document is not None
    assert result.selected_document.status is not ParseStatus.SUCCESS


def test_extract_docling_pictures_unknown_without_attr() -> None:
    visuals = extract_docling_page_visuals(SimpleNamespace(), page_numbers=[1, 2])
    assert all(v.image_visibility is ImageVisibility.UNKNOWN for v in visuals)


def test_backend_equivalence_with_same_visual_facts() -> None:
    pages = [CanonicalPage(page_number=1, raw_text="")]
    visuals = [
        PageVisualSignals(page_number=1, image_count=3, image_visibility=ImageVisibility.KNOWN)
    ]
    a = apply_post_parse_quality_gate(_doc(pages, backend="pypdf"), page_visuals=visuals)
    b = apply_post_parse_quality_gate(_doc(pages, backend="docling"), page_visuals=visuals)
    assert a.summary.page_states == b.summary.page_states == [PageQualityState.OCR_REQUIRED]


def test_cached_blocking_candidate_rejected(tmp_path: Path) -> None:
    pages = [CanonicalPage(page_number=1, raw_text="")]
    visuals = [
        PageVisualSignals(page_number=1, image_count=2, image_visibility=ImageVisibility.KNOWN)
    ]
    gated = apply_post_parse_quality_gate(_doc(pages, backend="docling"), page_visuals=visuals)
    forged = gated.document.model_copy(update={"status": ParseStatus.SUCCESS})
    cache = LocalParseCache(tmp_path / "cache")
    identity = forged.parser
    with pytest.raises(ParseCacheError):
        cache.put(
            forged,
            source_sha256=forged.source_sha256,
            parser=identity,
            page_quality_summary=gated.summary,
        )
    cache.put(
        gated.document,
        source_sha256=gated.document.source_sha256,
        parser=identity,
        page_quality_summary=gated.summary,
    )
    hit = cache.get(source_sha256=gated.document.source_sha256, parser=identity)
    assert hit.status is CacheGetStatus.HIT
    assert hit.document is not None
    assert hit.document.status is not ParseStatus.SUCCESS
    assert PAGE_QUALITY_GATE_VERSION == "halyk.page_quality_gate.v2"
