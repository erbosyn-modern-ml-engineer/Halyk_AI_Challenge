"""FAST pypdf parser acceptance tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from halyk_agent.adapters.archive.hashing import sha256_bytes
from halyk_agent.adapters.parsing.limits import ParserLimits
from halyk_agent.adapters.parsing.pypdf_parser import PyPdfDocumentParser
from halyk_agent.domain.parsing import BlockKind, ParseStatus
from tests.parsing.helpers import make_empty_page_pdf, make_text_pdf


def _parse(data: bytes, name: str = "doc.pdf"):
    parser = PyPdfDocumentParser()
    return parser.parse_canonical(
        data,
        source_file=name,
        artifact_id="art-1",
        source_sha256=sha256_bytes(data),
        media_type="application/pdf",
    )


def test_one_page_pdf_text_parses() -> None:
    doc = _parse(make_text_pdf(["Hello Stage 3"]))
    assert doc.status in {ParseStatus.SUCCESS, ParseStatus.PARTIAL}
    assert len(doc.pages) == 1
    assert "Hello Stage 3" in doc.pages[0].raw_text


def test_multi_page_preserves_order() -> None:
    doc = _parse(make_text_pdf(["PageA", "PageB", "PageC"]))
    assert [p.page_number for p in doc.pages] == [1, 2, 3]
    assert "PageA" in doc.pages[0].raw_text
    assert "PageB" in doc.pages[1].raw_text
    assert "PageC" in doc.pages[2].raw_text


def test_page_numbers_are_one_based() -> None:
    doc = _parse(make_text_pdf(["only"]))
    assert doc.pages[0].page_number == 1


def test_empty_page_represented_safely() -> None:
    doc = _parse(make_empty_page_pdf())
    assert len(doc.pages) == 1
    assert doc.pages[0].raw_text == ""
    assert doc.pages[0].blocks == []


def test_unicode_text_preserved() -> None:
    # Latin-1 + Greek omega are representable with default PDF fonts used by reportlab.
    doc = _parse(make_text_pdf(["Cafe resume Omega: \u03a9"]))
    assert "Cafe" in doc.pages[0].raw_text
    assert "\u03a9" in doc.pages[0].raw_text


def test_extract_text_none_handled() -> None:
    data = make_text_pdf(["x"])
    parser = PyPdfDocumentParser()
    with patch("halyk_agent.adapters.parsing.pypdf_parser.PdfReader") as reader_cls:
        page = MagicMock()
        page.extract_text.return_value = None
        reader = MagicMock()
        reader.is_encrypted = False
        reader.pages = [page]
        reader_cls.return_value = reader
        doc = parser.parse_canonical(
            data,
            source_file="doc.pdf",
            artifact_id="a",
            source_sha256=sha256_bytes(data),
        )
    assert doc.pages[0].raw_text == ""
    assert any(w.code.value == "EXTRACT_TEXT_NONE" for w in doc.warnings)


def test_encrypted_pdf_returns_encrypted_status() -> None:
    data = make_text_pdf(["secret"])
    parser = PyPdfDocumentParser()
    with patch("halyk_agent.adapters.parsing.pypdf_parser.PdfReader") as reader_cls:
        reader = MagicMock()
        reader.is_encrypted = True
        reader.decrypt.return_value = 0
        reader.pages = []
        reader_cls.return_value = reader
        doc = parser.parse_canonical(
            data,
            source_file="enc.pdf",
            artifact_id="a",
            source_sha256=sha256_bytes(data),
        )
    assert doc.status is ParseStatus.ENCRYPTED
    assert doc.pages == []


def test_malformed_pdf_does_not_crash() -> None:
    doc = _parse(b"not-a-pdf", name="bad.pdf")
    assert doc.status is ParseStatus.FAILED
    assert doc.pages == []


def test_page_limit_enforced() -> None:
    parser = PyPdfDocumentParser(limits=ParserLimits(max_pdf_pages=1))
    data = make_text_pdf(["a", "b"])
    doc = parser.parse_canonical(
        data,
        source_file="doc.pdf",
        artifact_id="a",
        source_sha256=sha256_bytes(data),
    )
    assert len(doc.pages) == 1
    assert doc.status is ParseStatus.PARTIAL


def test_document_character_limit_enforced() -> None:
    parser = PyPdfDocumentParser(limits=ParserLimits(max_document_characters=5))
    data = make_text_pdf(["ABCDEFGHIJ"])
    doc = parser.parse_canonical(
        data,
        source_file="doc.pdf",
        artifact_id="a",
        source_sha256=sha256_bytes(data),
    )
    assert doc.status is ParseStatus.PARTIAL
    assert doc.metrics.total_character_count <= 5


def test_fast_parser_emits_no_invented_bbox() -> None:
    doc = _parse(make_text_pdf(["bbox-free"]))
    assert all(block.bbox is None for page in doc.pages for block in page.blocks)


def test_page_text_block_offsets_match() -> None:
    doc = _parse(make_text_pdf(["ExactOffset"]))
    page = doc.pages[0]
    assert page.blocks
    block = page.blocks[0]
    assert block.kind is BlockKind.PAGE_TEXT
    assert page.raw_text[block.char_start : block.char_end] == block.raw_text


def test_repeated_parse_identical_ids() -> None:
    data = make_text_pdf(["stable"])
    first = _parse(data)
    second = _parse(data)
    assert first.id == second.id
    assert first.document_version_id == second.document_version_id
    assert first.pages[0].blocks[0].id == second.pages[0].blocks[0].id


def test_fast_never_imports_docling() -> None:
    import sys

    before = {name for name in sys.modules if name.startswith("docling")}
    _parse(make_text_pdf(["no-docling"]))
    after = {name for name in sys.modules if name.startswith("docling")}
    # Allow pre-existing modules from other tests; ensure this call didn't add them
    # when they were absent. If already imported by full suite, skip strict assert.
    if not before:
        assert not after
