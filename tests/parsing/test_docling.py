"""Docling adapter tests (lazy import + mapping)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from halyk_agent.adapters.parsing.docling_mapping import (
    convert_bbox_to_top_left,
    map_docling_document,
    map_docling_label_to_block_kind,
)
from halyk_agent.adapters.parsing.docling_parser import (
    DoclingDocumentParser,
    ensure_docling_available,
)
from halyk_agent.adapters.parsing.errors import ParserDependencyMissingError
from halyk_agent.domain.parsing import BlockKind, CoordinateOrigin


def test_missing_full_extra_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "docling" or name.startswith("docling."):
            raise ImportError("no docling")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ParserDependencyMissingError):
        ensure_docling_available()


def test_mocked_docling_text_item_maps_to_block() -> None:
    item = SimpleNamespace(
        self_ref="#/texts/0",
        label="paragraph",
        text="Hello Docling",
        prov=[SimpleNamespace(page_no=1, bbox=None)],
    )
    doc = SimpleNamespace(
        pages={1: SimpleNamespace(size=SimpleNamespace(width=100, height=200))},
        texts=[item],
        tables=[],
    )
    pages, _warnings = map_docling_document(doc, document_id="doc1")
    assert pages[0].page_number == 1
    assert pages[0].blocks[0].raw_text == "Hello Docling"
    assert pages[0].blocks[0].kind is BlockKind.PARAGRAPH


def test_docling_page_provenance_maps_correct_page() -> None:
    item = SimpleNamespace(
        self_ref="#/texts/0",
        label="text",
        text="p2",
        prov=[SimpleNamespace(page_no=2, bbox=None)],
    )
    doc = SimpleNamespace(
        pages={
            1: SimpleNamespace(size=SimpleNamespace(width=10, height=10)),
            2: SimpleNamespace(size=SimpleNamespace(width=10, height=10)),
        },
        texts=[item],
        tables=[],
    )
    pages, _ = map_docling_document(doc, document_id="doc1")
    assert any(p.page_number == 2 and p.blocks and p.blocks[0].raw_text == "p2" for p in pages)


def test_coordinate_origin_conversion() -> None:
    box = convert_bbox_to_top_left(
        left=10,
        top=20,
        right=30,
        bottom=40,
        page_width=100,
        page_height=200,
        origin="BOTTOMLEFT",
    )
    assert box is not None
    assert box.origin is CoordinateOrigin.TOP_LEFT
    assert box.top == 160.0
    assert box.bottom == 180.0


def test_table_cells_retain_row_column_span() -> None:
    cell = SimpleNamespace(text="c", row_span=2, col_span=3, self_ref=None)
    table = SimpleNamespace(
        self_ref="#/tables/0",
        prov=[SimpleNamespace(page_no=1, bbox=None)],
        data=SimpleNamespace(grid=[[cell]]),
        caption_text=None,
    )
    doc = SimpleNamespace(pages={1: SimpleNamespace(size=None)}, texts=[], tables=[table])
    pages, _ = map_docling_document(doc, document_id="doc1")
    t = pages[0].tables[0]
    assert t.cells[0].row_index == 0
    assert t.cells[0].column_index == 0
    assert t.cells[0].row_span == 2
    assert t.cells[0].column_span == 3


def test_missing_provenance_creates_warning_not_bbox() -> None:
    item = SimpleNamespace(self_ref="#/texts/0", label="paragraph", text="x", prov=[])
    doc = SimpleNamespace(pages={}, texts=[item], tables=[])
    pages, warnings = map_docling_document(doc, document_id="doc1")
    assert pages[0].blocks[0].bbox is None
    assert any(w.code.value == "MISSING_PROVENANCE" for w in warnings)


def test_native_items_used_not_markdown() -> None:
    assert map_docling_label_to_block_kind("section_header") is BlockKind.HEADING


@pytest.mark.docling
def test_real_tiny_pdf_docling_smoke() -> None:
    from halyk_agent.adapters.archive.hashing import sha256_bytes
    from tests.parsing.helpers import make_text_pdf

    try:
        ensure_docling_available()
    except ParserDependencyMissingError:
        pytest.skip("Docling full extra is not installed in this environment")
    data = make_text_pdf(["Docling smoke text"])
    parser = DoclingDocumentParser(ocr_enabled=False, table_structure_enabled=True)
    doc = parser.parse_canonical(
        data,
        source_file="smoke.pdf",
        artifact_id="smoke",
        source_sha256=sha256_bytes(data),
    )
    if not doc.pages and any(
        "LocalEntryNotFoundError" in warning.message for warning in doc.warnings
    ):
        pytest.skip("Docling model artifacts are unavailable in this runner cache")
    assert doc.pages
    assert any(p.raw_text for p in doc.pages)
