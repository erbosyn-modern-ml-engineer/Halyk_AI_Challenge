"""Canonical model invariant tests."""

from __future__ import annotations

import json
import math

import pytest

from halyk_agent.adapters.parsing.text_normalization import normalize_text
from halyk_agent.domain.parsing import (
    BlockKind,
    CanonicalBlock,
    CanonicalBoundingBox,
    CanonicalDocument,
    CanonicalPage,
    CanonicalTable,
    CanonicalTableCell,
    CoordinateOrigin,
    ParserIdentity,
    ParserKind,
    ParseStatus,
    block_identity,
    compute_metrics,
    document_identity,
    empty_metrics,
    table_cell_identity,
    table_identity,
)


def _parser() -> ParserIdentity:
    return ParserIdentity(
        kind=ParserKind.PYPDF,
        package_name="pypdf",
        package_version="6.0.0",
        configuration_hash="abc",
    )


def test_invalid_bbox_rejected() -> None:
    with pytest.raises(ValueError):
        CanonicalBoundingBox(
            left=10,
            top=10,
            right=5,
            bottom=20,
            page_width=100,
            page_height=100,
            origin=CoordinateOrigin.TOP_LEFT,
        )


def test_out_of_page_bbox_rejected() -> None:
    with pytest.raises(ValueError):
        CanonicalBoundingBox(
            left=0,
            top=0,
            right=200,
            bottom=10,
            page_width=100,
            page_height=100,
            origin=CoordinateOrigin.TOP_LEFT,
        )


def test_blocks_sorted_deterministically() -> None:
    doc_id = "d1"
    b1 = CanonicalBlock(
        id=block_identity(doc_id, 1, 1, BlockKind.PARAGRAPH, "b", None),
        page_number=1,
        ordinal=1,
        kind=BlockKind.PARAGRAPH,
        raw_text="b",
        normalized_text="b",
        char_start=2,
        char_end=3,
        source_parser=ParserKind.PYPDF,
    )
    b0 = CanonicalBlock(
        id=block_identity(doc_id, 1, 0, BlockKind.PARAGRAPH, "a", None),
        page_number=1,
        ordinal=0,
        kind=BlockKind.PARAGRAPH,
        raw_text="a",
        normalized_text="a",
        char_start=0,
        char_end=1,
        source_parser=ParserKind.PYPDF,
    )
    page = CanonicalPage(
        page_number=1,
        raw_text="a\nb",
        normalized_text="a\nb",
        blocks=[b1, b0],
    )
    assert [b.ordinal for b in page.blocks] == [0, 1]


def test_tables_and_cells_sorted_deterministically() -> None:
    table_id = table_identity("d1", 0, [1], None)
    c1 = CanonicalTableCell(
        id=table_cell_identity(table_id, 0, 1, 1, 1, "y"),
        page_number=1,
        table_id=table_id,
        row_index=0,
        column_index=1,
        row_span=1,
        column_span=1,
        raw_text="y",
        normalized_text="y",
    )
    c0 = CanonicalTableCell(
        id=table_cell_identity(table_id, 0, 0, 1, 1, "x"),
        page_number=1,
        table_id=table_id,
        row_index=0,
        column_index=0,
        row_span=1,
        column_span=1,
        raw_text="x",
        normalized_text="x",
    )
    table = CanonicalTable(
        id=table_id,
        page_numbers=[1],
        ordinal=0,
        cells=[c1, c0],
        row_count=1,
        column_count=2,
    )
    assert [c.column_index for c in table.cells] == [0, 1]


def test_success_requires_pages() -> None:
    with pytest.raises(ValueError):
        CanonicalDocument(
            id="id",
            artifact_id="a",
            document_id="id",
            document_version_id="v",
            source_file="f.pdf",
            source_sha256="0" * 64,
            parser=_parser(),
            status=ParseStatus.SUCCESS,
            pages=[],
            metrics=empty_metrics(),
        )


def test_failed_may_retain_observational_untrusted_text() -> None:
    """FAILED means no trusted usable pages; blocking-page text may remain."""
    page = CanonicalPage(page_number=1, raw_text="HEADING ONLY", normalized_text="HEADING ONLY")
    doc = CanonicalDocument(
        id="id",
        artifact_id="a",
        document_id="id",
        document_version_id="v",
        source_file="f.pdf",
        source_sha256="0" * 64,
        parser=_parser(),
        status=ParseStatus.FAILED,
        pages=[page],
        metrics=compute_metrics([page]),
    )
    assert doc.status is ParseStatus.FAILED
    assert doc.pages[0].raw_text == "HEADING ONLY"


def test_canonical_json_no_nan_infinity() -> None:
    page = CanonicalPage(page_number=1, raw_text="ok", normalized_text="ok")
    doc = CanonicalDocument(
        id=document_identity("a", "1" * 64),
        artifact_id="a",
        document_id=document_identity("a", "1" * 64),
        document_version_id="v",
        source_file="f.pdf",
        source_sha256="1" * 64,
        parser=_parser(),
        status=ParseStatus.SUCCESS,
        pages=[page],
        metrics=compute_metrics([page]),
    )
    payload = doc.model_dump(mode="json")
    text = json.dumps(payload, allow_nan=False)
    assert "NaN" not in text
    assert "Infinity" not in text
    assert all(not (isinstance(v, float) and not math.isfinite(v)) for v in _walk_numbers(payload))


def _walk_numbers(obj):
    if isinstance(obj, dict):
        for value in obj.values():
            yield from _walk_numbers(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _walk_numbers(value)
    elif isinstance(obj, float):
        yield obj


def test_raw_and_normalized_text_remain_separate() -> None:
    raw = "A\u00a0B\r\nC  "
    normalized = normalize_text(raw)
    assert raw != normalized
    assert "\u00a0" in raw
    assert "\u00a0" not in normalized
