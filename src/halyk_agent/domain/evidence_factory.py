"""Exact evidence-span factory from canonical document content."""

from __future__ import annotations

from halyk_agent.domain.errors import EvidenceAlignmentError
from halyk_agent.domain.evidence import EvidenceSpan
from halyk_agent.domain.ids import deterministic_id, sha256_text
from halyk_agent.domain.parsing import (
    CanonicalBlock,
    CanonicalBoundingBox,
    CanonicalDocument,
    CanonicalPage,
    CanonicalTableCell,
)


def _bbox_tuple(
    bbox: CanonicalBoundingBox | None,
) -> tuple[float, float, float, float] | None:
    if bbox is None:
        return None
    return (bbox.left, bbox.top, bbox.right, bbox.bottom)


def _evidence_id(
    *,
    document_id: str,
    document_version_id: str,
    page_number: int,
    char_start: int | None,
    char_end: int | None,
    quote: str,
    block_id: str | None,
    table_id: str | None,
    row_index: int | None,
    column_index: int | None,
) -> str:
    return deterministic_id(
        "evidence-span-v1",
        document_id,
        document_version_id,
        page_number,
        char_start if char_start is not None else "",
        char_end if char_end is not None else "",
        sha256_text(quote),
        block_id or "",
        table_id or "",
        row_index if row_index is not None else "",
        column_index if column_index is not None else "",
    )


def create_block_span(
    document: CanonicalDocument,
    page: CanonicalPage,
    block: CanonicalBlock,
) -> EvidenceSpan:
    """Create an EvidenceSpan for a canonical block with exact quote alignment."""
    if block.page_number != page.page_number:
        raise EvidenceAlignmentError("block page does not match page")
    if not block.raw_text.strip():
        raise EvidenceAlignmentError("empty evidence selection rejected")
    if block.char_start is None or block.char_end is None:
        raise EvidenceAlignmentError("block offsets are required for evidence")
    quote = page.raw_text[block.char_start : block.char_end]
    if quote != block.raw_text:
        raise EvidenceAlignmentError("block quote does not match page substring")
    return EvidenceSpan(
        id=_evidence_id(
            document_id=document.document_id,
            document_version_id=document.document_version_id,
            page_number=page.page_number,
            char_start=block.char_start,
            char_end=block.char_end,
            quote=quote,
            block_id=block.id,
            table_id=None,
            row_index=None,
            column_index=None,
        ),
        source_file=document.source_file,
        document_id=document.document_id,
        document_version_id=document.document_version_id,
        page_number=page.page_number,
        quote=quote,
        char_start=block.char_start,
        char_end=block.char_end,
        bbox=_bbox_tuple(block.bbox),
        block_id=block.id,
    )


def create_table_cell_span(
    document: CanonicalDocument,
    cell: CanonicalTableCell,
) -> EvidenceSpan:
    """Create an EvidenceSpan for a table cell."""
    quote = cell.raw_text
    if not quote.strip():
        raise EvidenceAlignmentError("empty evidence selection rejected")
    return EvidenceSpan(
        id=_evidence_id(
            document_id=document.document_id,
            document_version_id=document.document_version_id,
            page_number=cell.page_number,
            char_start=None,
            char_end=None,
            quote=quote,
            block_id=None,
            table_id=cell.table_id,
            row_index=cell.row_index,
            column_index=cell.column_index,
        ),
        source_file=document.source_file,
        document_id=document.document_id,
        document_version_id=document.document_version_id,
        page_number=cell.page_number,
        quote=quote,
        bbox=_bbox_tuple(cell.bbox),
        table_id=cell.table_id,
        row_index=cell.row_index,
        column_index=cell.column_index,
    )


def create_exact_page_span(
    document: CanonicalDocument,
    page_number: int,
    char_start: int,
    char_end: int,
) -> EvidenceSpan:
    """Create evidence from an exact half-open page character range."""
    if char_start < 0 or char_end < 0:
        raise EvidenceAlignmentError("out-of-bounds range")
    if char_start >= char_end:
        raise EvidenceAlignmentError("empty evidence selection rejected")
    page = next((p for p in document.pages if p.page_number == page_number), None)
    if page is None:
        raise EvidenceAlignmentError("cross-page evidence rejected")
    if char_end > len(page.raw_text):
        raise EvidenceAlignmentError("out-of-bounds range")
    quote = page.raw_text[char_start:char_end]
    if not quote.strip():
        raise EvidenceAlignmentError("empty evidence selection rejected")
    # Reject normalized-only quotes that are not present as raw substrings.
    if quote not in page.raw_text:
        raise EvidenceAlignmentError("quote must exist in raw page text")
    return EvidenceSpan(
        id=_evidence_id(
            document_id=document.document_id,
            document_version_id=document.document_version_id,
            page_number=page_number,
            char_start=char_start,
            char_end=char_end,
            quote=quote,
            block_id=None,
            table_id=None,
            row_index=None,
            column_index=None,
        ),
        source_file=document.source_file,
        document_id=document.document_id,
        document_version_id=document.document_version_id,
        page_number=page_number,
        quote=quote,
        char_start=char_start,
        char_end=char_end,
    )


def build_evidence_catalog(document: CanonicalDocument) -> list[EvidenceSpan]:
    """Emit evidence spans for all non-empty blocks and table cells."""
    spans: list[EvidenceSpan] = []
    for page in document.pages:
        for block in page.blocks:
            if not block.raw_text.strip():
                continue
            if block.char_start is None or block.char_end is None:
                continue
            spans.append(create_block_span(document, page, block))
        for table in page.tables:
            for cell in table.cells:
                if not cell.raw_text.strip():
                    continue
                spans.append(create_table_cell_span(document, cell))
    spans.sort(key=lambda span: span.id)
    return spans
