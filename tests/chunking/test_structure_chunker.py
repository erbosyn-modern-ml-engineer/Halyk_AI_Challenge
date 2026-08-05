"""Structure-aware chunker acceptance tests."""

from __future__ import annotations

import json

from halyk_agent.adapters.chunking import (
    ChunkerConfig,
    StructureAwareChunker,
    build_chunk_manifest,
    build_chunker_identity,
)
from halyk_agent.domain.chunking import ChunkKind, ChunkLevel, RetrievalTextKind
from halyk_agent.domain.evidence_factory import build_evidence_catalog
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


def _parser() -> ParserIdentity:
    return ParserIdentity(
        kind=ParserKind.PYPDF,
        package_name="pypdf",
        package_version="1",
        configuration_hash="cfg",
    )


def _page_only_document(raw: str) -> CanonicalDocument:
    artifact = "art-page"
    sha = "b" * 64
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
    return CanonicalDocument(
        id=doc_id,
        artifact_id=artifact,
        document_id=doc_id,
        document_version_id="ver-page-1",
        source_file="page.pdf",
        source_sha256=sha,
        parser=_parser(),
        status=ParseStatus.SUCCESS,
        pages=[page],
        metrics=compute_metrics([page]),
    )


def _heading_document() -> CanonicalDocument:
    artifact = "art-head"
    sha = "c" * 64
    doc_id = document_identity(artifact, sha)
    heading_text = "Payment Rules"
    body = "Customers must provide original invoices for reimbursement."
    raw = f"{heading_text}\n{body}"
    heading = CanonicalBlock(
        id=block_identity(doc_id, 1, 0, BlockKind.HEADING, heading_text, None),
        page_number=1,
        ordinal=0,
        kind=BlockKind.HEADING,
        raw_text=heading_text,
        normalized_text=heading_text,
        char_start=0,
        char_end=len(heading_text),
        source_parser=ParserKind.DOCLING,
    )
    para = CanonicalBlock(
        id=block_identity(doc_id, 1, 1, BlockKind.PARAGRAPH, body, None),
        page_number=1,
        ordinal=1,
        kind=BlockKind.PARAGRAPH,
        raw_text=body,
        normalized_text=body,
        char_start=len(heading_text) + 1,
        char_end=len(raw),
        source_parser=ParserKind.DOCLING,
    )
    page = CanonicalPage(
        page_number=1,
        raw_text=raw,
        normalized_text=raw,
        blocks=[heading, para],
    )
    return CanonicalDocument(
        id=doc_id,
        artifact_id=artifact,
        document_id=doc_id,
        document_version_id="ver-head-1",
        source_file="headed.pdf",
        source_sha256=sha,
        parser=ParserIdentity(
            kind=ParserKind.DOCLING,
            package_name="docling",
            package_version="1",
            configuration_hash="d",
        ),
        status=ParseStatus.SUCCESS,
        pages=[page],
        metrics=compute_metrics([page]),
    )


def _table_document() -> CanonicalDocument:
    artifact = "art-table"
    sha = "d" * 64
    doc_id = document_identity(artifact, sha)
    table_id = table_identity(doc_id, 0, [1], None)
    cells = [
        CanonicalTableCell(
            id=table_cell_identity(table_id, 0, 0, 1, 1, "Item"),
            page_number=1,
            table_id=table_id,
            row_index=0,
            column_index=0,
            row_span=1,
            column_span=1,
            raw_text="Item",
            normalized_text="Item",
        ),
        CanonicalTableCell(
            id=table_cell_identity(table_id, 0, 1, 1, 1, "Amount"),
            page_number=1,
            table_id=table_id,
            row_index=0,
            column_index=1,
            row_span=1,
            column_span=1,
            raw_text="Amount",
            normalized_text="Amount",
        ),
        CanonicalTableCell(
            id=table_cell_identity(table_id, 1, 0, 1, 1, "Fee"),
            page_number=1,
            table_id=table_id,
            row_index=1,
            column_index=0,
            row_span=1,
            column_span=1,
            raw_text="Fee",
            normalized_text="Fee",
        ),
        CanonicalTableCell(
            id=table_cell_identity(table_id, 1, 1, 1, 1, "100"),
            page_number=1,
            table_id=table_id,
            row_index=1,
            column_index=1,
            row_span=1,
            column_span=1,
            raw_text="100",
            normalized_text="100",
        ),
    ]
    table = CanonicalTable(
        id=table_id,
        page_numbers=[1],
        ordinal=0,
        cells=cells,
        row_count=2,
        column_count=2,
        caption="Fees",
    )
    page = CanonicalPage(
        page_number=1,
        raw_text="table page",
        normalized_text="table page",
        blocks=[
            CanonicalBlock(
                id=block_identity(doc_id, 1, 0, BlockKind.PAGE_TEXT, "table page", None),
                page_number=1,
                ordinal=0,
                kind=BlockKind.PAGE_TEXT,
                raw_text="table page",
                normalized_text="table page",
                char_start=0,
                char_end=10,
                source_parser=ParserKind.DOCLING,
            )
        ],
        tables=[table],
    )
    return CanonicalDocument(
        id=doc_id,
        artifact_id=artifact,
        document_id=doc_id,
        document_version_id="ver-table-1",
        source_file="table.pdf",
        source_sha256=sha,
        parser=_parser(),
        status=ParseStatus.SUCCESS,
        pages=[page],
        metrics=compute_metrics([page]),
    )


def test_deterministic_chunk_ids() -> None:
    document = _page_only_document("Deterministic page text for chunking stability checks.")
    evidence = build_evidence_catalog(document)
    chunker = StructureAwareChunker()
    first = chunker.chunk_document(document, evidence)
    second = chunker.chunk_document(document, evidence)
    assert [c.id for c in first] == [c.id for c in second]
    assert [c.model_dump(mode="json") for c in first] == [c.model_dump(mode="json") for c in second]


def test_parent_child_link() -> None:
    document = _page_only_document(
        "Parent child linkage text that is long enough for a page chunk."
    )
    evidence = build_evidence_catalog(document)
    chunks = StructureAwareChunker().chunk_document(document, evidence)
    parents = [c for c in chunks if c.level is ChunkLevel.PARENT]
    children = [c for c in chunks if c.level is ChunkLevel.CHILD]
    assert parents
    assert children
    parent_ids = {p.id for p in parents}
    assert all(child.parent_chunk_id in parent_ids for child in children)


def test_every_chunk_has_evidence() -> None:
    document = _page_only_document(
        "Evidence must be present on every produced retrieval chunk unit."
    )
    evidence = build_evidence_catalog(document)
    chunks = StructureAwareChunker().chunk_document(document, evidence)
    assert chunks
    assert all(chunk.evidence_span_ids for chunk in chunks)


def test_page_only_split_preserves_offsets() -> None:
    # Force multiple children with a small child max.
    raw = ("Alpha sentence one. Beta sentence two. Gamma sentence three. " * 8).strip()
    document = _page_only_document(raw)
    evidence = build_evidence_catalog(document)
    config = ChunkerConfig(
        parent_max_characters=4000,
        child_max_characters=80,
        child_overlap_characters=10,
        minimum_chunk_characters=10,
        maximum_chunk_characters=6000,
    )
    chunks = StructureAwareChunker(config).chunk_document(document, evidence)
    children = [c for c in chunks if c.level is ChunkLevel.CHILD]
    assert len(children) >= 2
    for child in children:
        assert child.kind is ChunkKind.PAGE
        assert child.retrieval_text_kind is RetrievalTextKind.RAW_SOURCE
        assert child.raw_text == child.retrieval_text
        start = int(child.metadata["char_start"])
        end = int(child.metadata["char_end"])
        assert document.pages[0].raw_text[start:end] == child.raw_text


def test_heading_context_enriches_retrieval_not_raw() -> None:
    document = _heading_document()
    evidence = build_evidence_catalog(document)
    chunks = StructureAwareChunker().chunk_document(document, evidence)
    enriched = [c for c in chunks if c.retrieval_text_kind is RetrievalTextKind.CONTEXT_ENRICHED]
    assert enriched
    for chunk in enriched:
        assert "Payment Rules" in chunk.retrieval_text
        assert chunk.raw_text.startswith("Customers must provide")
        assert not chunk.raw_text.startswith("Payment Rules")


def test_table_synthetic_kind() -> None:
    document = _table_document()
    evidence = build_evidence_catalog(document)
    chunks = StructureAwareChunker().chunk_document(document, evidence)
    table_chunks = [c for c in chunks if c.kind is ChunkKind.TABLE]
    assert table_chunks
    parent = table_chunks[0]
    assert parent.retrieval_text_kind is RetrievalTextKind.SYNTHETIC_TABLE_SERIALIZATION
    assert "Caption: Fees" in parent.retrieval_text
    assert "Columns: Item | Amount" in parent.retrieval_text
    assert parent.raw_text != parent.retrieval_text
    rows = [c for c in chunks if c.kind is ChunkKind.TABLE_ROW]
    assert rows
    assert all(c.parent_chunk_id == parent.id for c in rows)


def test_empty_chunks_rejected() -> None:
    artifact = "art-empty"
    sha = "e" * 64
    doc_id = document_identity(artifact, sha)
    page = CanonicalPage(page_number=1, raw_text="", normalized_text="", blocks=[])
    document = CanonicalDocument(
        id=doc_id,
        artifact_id=artifact,
        document_id=doc_id,
        document_version_id="ver-empty",
        source_file="empty.pdf",
        source_sha256=sha,
        parser=_parser(),
        status=ParseStatus.SUCCESS,
        pages=[page],
        metrics=compute_metrics([page]),
    )
    chunks = StructureAwareChunker().chunk_document(document, [])
    assert chunks == []

    # Whitespace-only page text must not yield chunks.
    blank = _page_only_document("   \n\t  ")
    blank_evidence = build_evidence_catalog(blank)
    assert blank_evidence == []
    assert StructureAwareChunker().chunk_document(blank, blank_evidence) == []


def test_config_change_changes_ids() -> None:
    document = _page_only_document("Config sensitive chunk identity payload text for hashing.")
    evidence = build_evidence_catalog(document)
    default_ids = {c.id for c in StructureAwareChunker().chunk_document(document, evidence)}
    alt = StructureAwareChunker(ChunkerConfig(child_max_characters=200))
    alt_ids = {c.id for c in alt.chunk_document(document, evidence)}
    assert default_ids != alt_ids
    assert (
        build_chunker_identity().configuration_hash
        != build_chunker_identity(ChunkerConfig(child_max_characters=200)).configuration_hash
    )


def test_build_chunk_manifest() -> None:
    document = _page_only_document("Manifest summary over produced retrieval chunks.")
    evidence = build_evidence_catalog(document)
    chunker = StructureAwareChunker()
    chunks = chunker.chunk_document(document, evidence)
    manifest = build_chunk_manifest(chunks, chunker.identity, source_parse_report_hash="report" * 8)
    assert manifest.total_chunks == len(chunks)
    assert manifest.parent_chunks >= 1
    assert manifest.child_chunks >= 1
    assert document.document_id in manifest.documents
    # Stable serialization
    assert json.loads(manifest.model_dump_json())["chunks_sha256"] == manifest.chunks_sha256
