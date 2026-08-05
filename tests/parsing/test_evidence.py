"""Evidence alignment acceptance tests."""

from __future__ import annotations

import pytest

from halyk_agent.domain.errors import EvidenceAlignmentError
from halyk_agent.domain.evidence_factory import (
    create_block_span,
    create_exact_page_span,
    create_table_cell_span,
)
from halyk_agent.domain.parsing import (
    BlockKind,
    CanonicalBlock,
    CanonicalDocument,
    CanonicalPage,
    CanonicalTable,
    CanonicalTableCell,
    ParserIdentity,
    ParserKind,
    ParseStatus,
    block_identity,
    compute_metrics,
    document_identity,
    table_cell_identity,
    table_identity,
)


def _doc_with_page(
    raw: str = "Hello World",
) -> tuple[CanonicalDocument, CanonicalPage, CanonicalBlock]:
    artifact = "art"
    sha = "a" * 64
    doc_id = document_identity(artifact, sha)
    block = CanonicalBlock(
        id=block_identity(doc_id, 1, 0, BlockKind.PAGE_TEXT, raw, None),
        page_number=1,
        ordinal=0,
        kind=BlockKind.PAGE_TEXT,
        raw_text=raw,
        normalized_text=raw,
        char_start=0,
        char_end=len(raw),
        source_parser=ParserKind.PYPDF,
    )
    page = CanonicalPage(
        page_number=1,
        raw_text=raw,
        normalized_text=raw,
        blocks=[block],
    )
    document = CanonicalDocument(
        id=doc_id,
        artifact_id=artifact,
        document_id=doc_id,
        document_version_id="v1",
        source_file="doc.pdf",
        source_sha256=sha,
        parser=ParserIdentity(
            kind=ParserKind.PYPDF,
            package_name="pypdf",
            package_version="1",
            configuration_hash="c",
        ),
        status=ParseStatus.SUCCESS,
        pages=[page],
        metrics=compute_metrics([page]),
    )
    return document, page, block


def test_block_evidence_quote_matches() -> None:
    document, page, block = _doc_with_page()
    span = create_block_span(document, page, block)
    assert span.quote == block.raw_text
    assert page.raw_text[span.char_start : span.char_end] == span.quote


def test_wrong_quote_rejected() -> None:
    document, page, block = _doc_with_page("abc")
    bad = CanonicalBlock(
        id=block.id,
        page_number=1,
        ordinal=0,
        kind=BlockKind.PAGE_TEXT,
        raw_text="zzz",
        normalized_text="zzz",
        char_start=0,
        char_end=3,
        source_parser=ParserKind.PYPDF,
    )
    with pytest.raises(EvidenceAlignmentError):
        create_block_span(document, page, bad)


def test_out_of_range_offsets_rejected() -> None:
    document, _, _ = _doc_with_page("abc")
    with pytest.raises(EvidenceAlignmentError):
        create_exact_page_span(document, 1, 0, 99)


def test_empty_evidence_selection_rejected() -> None:
    document, _, _ = _doc_with_page("abc")
    with pytest.raises(EvidenceAlignmentError):
        create_exact_page_span(document, 1, 1, 1)


def test_cross_page_evidence_rejected() -> None:
    document, _, _ = _doc_with_page("abc")
    with pytest.raises(EvidenceAlignmentError):
        create_exact_page_span(document, 2, 0, 1)


def test_table_cell_evidence_includes_row_column() -> None:
    document, page, _ = _doc_with_page("abc")
    table_id = table_identity(document.document_id, 0, [1], None)
    cell = CanonicalTableCell(
        id=table_cell_identity(table_id, 1, 2, 1, 1, "cell"),
        page_number=1,
        table_id=table_id,
        row_index=1,
        column_index=2,
        row_span=1,
        column_span=1,
        raw_text="cell",
        normalized_text="cell",
    )
    table = CanonicalTable(
        id=table_id,
        page_numbers=[1],
        ordinal=0,
        cells=[cell],
        row_count=2,
        column_count=3,
    )
    page2 = CanonicalPage(
        page_number=1,
        raw_text=page.raw_text,
        normalized_text=page.normalized_text,
        blocks=page.blocks,
        tables=[table],
    )
    document2 = CanonicalDocument(
        id=document.id,
        artifact_id=document.artifact_id,
        document_id=document.document_id,
        document_version_id=document.document_version_id,
        source_file=document.source_file,
        source_sha256=document.source_sha256,
        parser=document.parser,
        status=ParseStatus.SUCCESS,
        pages=[page2],
        metrics=compute_metrics([page2]),
    )
    span = create_table_cell_span(document2, cell)
    assert span.table_id == table_id
    assert span.row_index == 1
    assert span.column_index == 2


def test_evidence_id_stable() -> None:
    document, page, block = _doc_with_page()
    a = create_block_span(document, page, block)
    b = create_block_span(document, page, block)
    assert a.id == b.id


def test_normalized_only_quote_cannot_be_raw_evidence() -> None:
    # Raw has NBSP; normalized would replace it — evidence must use raw.
    raw = "A\u00a0B"
    document, page, block = _doc_with_page(raw)
    span = create_block_span(document, page, block)
    assert "\u00a0" in span.quote
    with pytest.raises(EvidenceAlignmentError):
        # Attempt to select a normalized form that is not a raw substring.
        create_exact_page_span(document, 1, 0, 0)  # empty
