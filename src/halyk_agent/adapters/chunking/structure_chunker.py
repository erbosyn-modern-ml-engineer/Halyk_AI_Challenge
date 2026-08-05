"""Deterministic structure-aware parent/child/atomic chunking."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from halyk_agent.adapters.chunking.length_estimation import estimate_token_count
from halyk_agent.adapters.chunking.table_serialization import (
    serialize_row,
    serialize_table,
    table_raw_text,
)
from halyk_agent.adapters.parsing.text_normalization import NORMALIZATION_VERSION
from halyk_agent.domain.chunking import (
    CHUNK_MANIFEST_SCHEMA_VERSION,
    CHUNK_SCHEMA_VERSION,
    ChunkerIdentity,
    ChunkKind,
    ChunkLevel,
    ChunkManifest,
    RetrievalChunk,
    RetrievalTextKind,
    chunk_identity,
    chunks_content_sha256,
)
from halyk_agent.domain.evidence import EvidenceSpan
from halyk_agent.domain.evidence_factory import create_exact_page_span
from halyk_agent.domain.parsing import (
    BlockKind,
    CanonicalBlock,
    CanonicalDocument,
    CanonicalPage,
    CanonicalTable,
    configuration_hash,
)

CHUNKER_NAME = "structure-aware"
CHUNKER_VERSION = "1.0.0"

_HEADING_KINDS = frozenset(
    {
        BlockKind.TITLE,
        BlockKind.HEADING,
        BlockKind.HEADER,
    }
)
# Include fullwidth stop (U+FF0E) intentionally for CJK/mixed text boundaries.
_SENTENCE_PUNCT = frozenset(".!?。．؟!…")  # noqa: RUF001


@dataclass(frozen=True, slots=True)
class ChunkerConfig:
    """Configurable chunk size targets (not immutable constants)."""

    parent_max_characters: int = 4000
    child_max_characters: int = 1200
    child_overlap_characters: int = 120
    minimum_chunk_characters: int = 40
    maximum_chunk_characters: int = 6000
    emit_table_cell_atomics: bool = True

    def __post_init__(self) -> None:
        if self.parent_max_characters < 1:
            raise ValueError("parent_max_characters must be >= 1")
        if self.child_max_characters < 1:
            raise ValueError("child_max_characters must be >= 1")
        if self.child_overlap_characters < 0:
            raise ValueError("child_overlap_characters must be >= 0")
        if self.minimum_chunk_characters < 1:
            raise ValueError("minimum_chunk_characters must be >= 1")
        if self.maximum_chunk_characters < self.child_max_characters:
            raise ValueError("maximum_chunk_characters must be >= child_max_characters")
        if self.child_overlap_characters >= self.child_max_characters:
            raise ValueError("child_overlap_characters must be < child_max_characters")

    def to_hash_payload(self) -> dict[str, int | bool]:
        return asdict(self)


def build_chunker_identity(config: ChunkerConfig | None = None) -> ChunkerIdentity:
    """Build ChunkerIdentity including configuration hash."""
    cfg = config or ChunkerConfig()
    return ChunkerIdentity(
        name=CHUNKER_NAME,
        version=CHUNKER_VERSION,
        configuration_hash=configuration_hash(cfg.to_hash_payload()),
        normalization_version=NORMALIZATION_VERSION,
    )


def build_chunk_manifest(
    chunks: list[RetrievalChunk],
    chunker_identity: ChunkerIdentity,
    source_parse_report_hash: str,
) -> ChunkManifest:
    """Build a deterministic ChunkManifest for a chunking run."""
    table_kinds = {ChunkKind.TABLE, ChunkKind.TABLE_ROW, ChunkKind.TABLE_CELL}
    return ChunkManifest(
        schema_version=CHUNK_MANIFEST_SCHEMA_VERSION,
        chunker_identity=chunker_identity,
        source_parse_report_hash=source_parse_report_hash,
        documents=sorted({chunk.document_id for chunk in chunks}),
        total_chunks=len(chunks),
        parent_chunks=sum(1 for chunk in chunks if chunk.level is ChunkLevel.PARENT),
        child_chunks=sum(1 for chunk in chunks if chunk.level is ChunkLevel.CHILD),
        table_chunks=sum(1 for chunk in chunks if chunk.kind in table_kinds),
        chunks_sha256=chunks_content_sha256(chunks),
    )


def _is_page_only(page: CanonicalPage) -> bool:
    """True when the page has no semantic headings (typical FAST PAGE_TEXT)."""
    if not page.blocks:
        return bool(page.raw_text.strip())
    if any(block.kind in _HEADING_KINDS for block in page.blocks):
        return False
    kinds = {block.kind for block in page.blocks}
    return kinds <= {BlockKind.PAGE_TEXT, BlockKind.UNKNOWN, BlockKind.TABLE}


def _preferred_boundary(text: str, start: int, hard_end: int) -> int:
    """Pick the best split end in ``(start, hard_end]``; never mid codepoint.

    Preference: paragraph, line, sentence punctuation, whitespace, hard cut.
    Python ``str`` indexing is already by Unicode code point.
    """
    if hard_end >= len(text):
        return len(text)
    if hard_end <= start:
        return min(start + 1, len(text)) if start < len(text) else len(text)

    window = text[start:hard_end]
    # Paragraph boundary
    idx = window.rfind("\n\n")
    if idx > 0:
        return start + idx + 2
    # Line boundary
    idx = window.rfind("\n")
    if idx > 0:
        return start + idx + 1
    # Sentence punctuation followed by whitespace or end
    for i in range(len(window) - 1, 0, -1):
        ch = window[i]
        if ch in _SENTENCE_PUNCT and (i + 1 >= len(window) or window[i + 1].isspace()):
            return start + i + 1
    # Whitespace
    for i in range(len(window) - 1, 0, -1):
        if window[i].isspace():
            return start + i + 1
    return hard_end


def _split_ranges(
    text: str,
    *,
    max_characters: int,
    overlap_characters: int,
    minimum_chunk_characters: int,
) -> list[tuple[int, int]]:
    """Split ``text`` into half-open ranges with overlap and boundary preference."""
    length = len(text)
    if length == 0:
        return []
    if length <= max_characters:
        return [(0, length)]

    ranges: list[tuple[int, int]] = []
    start = 0
    while start < length:
        hard_end = min(start + max_characters, length)
        end = _preferred_boundary(text, start, hard_end) if hard_end < length else length
        if end <= start:
            end = hard_end
        # Avoid tiny trailing fragments when possible by extending previous.
        remaining = length - end
        if (
            ranges
            and 0 < remaining < minimum_chunk_characters
            and (end - start) + remaining <= max_characters
        ):
            end = length
        ranges.append((start, end))
        if end >= length:
            break
        next_start = max(end - overlap_characters, start + 1)
        if next_start >= end:
            next_start = end
        start = next_start
    return ranges


def _overlaps(a_start: int, a_end: int, b_start: int | None, b_end: int | None) -> bool:
    if b_start is None or b_end is None:
        return False
    return a_start < b_end and b_start < a_end


def _evidence_ids_for_range(
    document: CanonicalDocument,
    evidence_spans: list[EvidenceSpan],
    *,
    page_number: int,
    char_start: int,
    char_end: int,
    created: dict[tuple[int, int, int], EvidenceSpan],
) -> list[str]:
    """Link overlapping catalog spans; create exact page span if none apply."""
    ids: list[str] = []
    for span in evidence_spans:
        if span.page_number != page_number:
            continue
        if _overlaps(char_start, char_end, span.char_start, span.char_end):
            ids.append(span.id)
        elif span.char_start is None and span.block_id is None and span.table_id is None:
            continue
    if ids:
        return sorted(set(ids))

    key = (page_number, char_start, char_end)
    if key not in created:
        quote = ""
        page = next(p for p in document.pages if p.page_number == page_number)
        quote = page.raw_text[char_start:char_end]
        if not quote.strip():
            return []
        created[key] = create_exact_page_span(document, page_number, char_start, char_end)
    return [created[key].id]


def _evidence_ids_for_block(
    evidence_spans: list[EvidenceSpan],
    block: CanonicalBlock,
) -> list[str]:
    ids = [span.id for span in evidence_spans if span.block_id == block.id]
    if ids:
        return sorted(set(ids))
    # Fall back to overlapping page ranges when catalog uses page spans only.
    if block.char_start is None or block.char_end is None:
        return []
    return sorted(
        {
            span.id
            for span in evidence_spans
            if span.page_number == block.page_number
            and _overlaps(block.char_start, block.char_end, span.char_start, span.char_end)
        }
    )


def _evidence_ids_for_table(
    evidence_spans: list[EvidenceSpan],
    table: CanonicalTable,
) -> list[str]:
    return sorted({span.id for span in evidence_spans if span.table_id == table.id})


def _evidence_ids_for_row(
    evidence_spans: list[EvidenceSpan],
    table: CanonicalTable,
    row_index: int,
) -> list[str]:
    return sorted(
        {
            span.id
            for span in evidence_spans
            if span.table_id == table.id and span.row_index == row_index
        }
    )


def _evidence_ids_for_cell(
    evidence_spans: list[EvidenceSpan],
    table_id: str,
    row_index: int,
    column_index: int,
) -> list[str]:
    return sorted(
        {
            span.id
            for span in evidence_spans
            if span.table_id == table_id
            and span.row_index == row_index
            and span.column_index == column_index
        }
    )


class StructureAwareChunker:
    """Shared FAST/FULL structure-aware chunker."""

    def __init__(self, config: ChunkerConfig | None = None) -> None:
        self.config = config or ChunkerConfig()
        self.identity = build_chunker_identity(self.config)

    def chunk_document(
        self,
        document: CanonicalDocument,
        evidence_spans: list[EvidenceSpan],
    ) -> list[RetrievalChunk]:
        """Chunk a canonical document into parent/child/atomic retrieval units."""
        chunks: list[RetrievalChunk] = []
        ordinal = 0
        created_spans: dict[tuple[int, int, int], EvidenceSpan] = {}

        for page in document.pages:
            page_chunks, ordinal = self._chunk_page(
                document,
                page,
                evidence_spans,
                ordinal=ordinal,
                created_spans=created_spans,
            )
            chunks.extend(page_chunks)

        chunks.sort(key=lambda item: (item.ordinal, item.id))
        return chunks

    def _make_chunk(
        self,
        *,
        document: CanonicalDocument,
        kind: ChunkKind,
        level: ChunkLevel,
        page_numbers: list[int],
        raw_text: str,
        retrieval_text: str,
        retrieval_text_kind: RetrievalTextKind,
        parent_chunk_id: str | None,
        source_block_ids: list[str],
        source_table_ids: list[str],
        evidence_span_ids: list[str],
        heading_path: list[str],
        ordinal: int,
        metadata: dict[str, object] | None = None,
    ) -> RetrievalChunk | None:
        if not raw_text.strip() or not retrieval_text.strip():
            return None
        if len(raw_text) > self.config.maximum_chunk_characters:
            # Callers should pre-split; refuse oversized units.
            raise ValueError(
                f"chunk exceeds maximum_chunk_characters ({self.config.maximum_chunk_characters})"
            )
        if not evidence_span_ids:
            return None

        chunk_id = chunk_identity(
            document_id=document.document_id,
            chunker_configuration_hash=self.identity.configuration_hash,
            level=level,
            kind=kind,
            page_numbers=page_numbers,
            raw_text=raw_text,
            retrieval_text=retrieval_text,
            source_block_ids=source_block_ids,
            source_table_ids=source_table_ids,
            evidence_span_ids=evidence_span_ids,
            ordinal=ordinal,
        )
        meta: dict[str, object] = {"chunk_schema_version": CHUNK_SCHEMA_VERSION}
        if metadata:
            meta.update(metadata)
        return RetrievalChunk(
            id=chunk_id,
            document_id=document.document_id,
            document_version_id=document.document_version_id,
            artifact_id=document.artifact_id,
            source_file=document.source_file,
            kind=kind,
            level=level,
            page_numbers=page_numbers,
            raw_text=raw_text,
            retrieval_text=retrieval_text,
            retrieval_text_kind=retrieval_text_kind,
            parent_chunk_id=parent_chunk_id,
            source_block_ids=source_block_ids,
            source_table_ids=source_table_ids,
            evidence_span_ids=evidence_span_ids,
            heading_path=heading_path,
            ordinal=ordinal,
            character_count=len(raw_text),
            estimated_token_count=estimate_token_count(raw_text),
            metadata=meta,  # type: ignore[arg-type]
        )

    def _chunk_page(
        self,
        document: CanonicalDocument,
        page: CanonicalPage,
        evidence_spans: list[EvidenceSpan],
        *,
        ordinal: int,
        created_spans: dict[tuple[int, int, int], EvidenceSpan],
    ) -> tuple[list[RetrievalChunk], int]:
        chunks: list[RetrievalChunk] = []

        if _is_page_only(page) and page.raw_text.strip():
            page_chunks, ordinal = self._chunk_page_only(
                document,
                page,
                evidence_spans,
                ordinal=ordinal,
                created_spans=created_spans,
            )
            chunks.extend(page_chunks)
        else:
            text_chunks, ordinal = self._chunk_heading_path(
                document,
                page,
                evidence_spans,
                ordinal=ordinal,
                created_spans=created_spans,
            )
            chunks.extend(text_chunks)

        for table in page.tables:
            table_chunks, ordinal = self._chunk_table(
                document,
                table,
                evidence_spans,
                ordinal=ordinal,
            )
            chunks.extend(table_chunks)

        return chunks, ordinal

    def _chunk_page_only(
        self,
        document: CanonicalDocument,
        page: CanonicalPage,
        evidence_spans: list[EvidenceSpan],
        *,
        ordinal: int,
        created_spans: dict[tuple[int, int, int], EvidenceSpan],
    ) -> tuple[list[RetrievalChunk], int]:
        chunks: list[RetrievalChunk] = []
        raw = page.raw_text
        block_ids = [block.id for block in page.blocks if block.kind is BlockKind.PAGE_TEXT]

        parent_ranges = _split_ranges(
            raw,
            max_characters=min(
                self.config.parent_max_characters, self.config.maximum_chunk_characters
            ),
            overlap_characters=0,
            minimum_chunk_characters=self.config.minimum_chunk_characters,
        )
        for parent_start, parent_end in parent_ranges:
            parent_raw = raw[parent_start:parent_end]
            if not parent_raw.strip():
                continue
            parent_evidence = _evidence_ids_for_range(
                document,
                evidence_spans,
                page_number=page.page_number,
                char_start=parent_start,
                char_end=parent_end,
                created=created_spans,
            )
            parent = self._make_chunk(
                document=document,
                kind=ChunkKind.PAGE,
                level=ChunkLevel.PARENT,
                page_numbers=[page.page_number],
                raw_text=parent_raw,
                retrieval_text=parent_raw,
                retrieval_text_kind=RetrievalTextKind.RAW_SOURCE,
                parent_chunk_id=None,
                source_block_ids=block_ids,
                source_table_ids=[],
                evidence_span_ids=parent_evidence,
                heading_path=[],
                ordinal=ordinal,
                metadata={"char_start": parent_start, "char_end": parent_end},
            )
            if parent is None:
                continue
            chunks.append(parent)
            ordinal += 1

            child_ranges = _split_ranges(
                parent_raw,
                max_characters=min(
                    self.config.child_max_characters,
                    self.config.maximum_chunk_characters,
                ),
                overlap_characters=self.config.child_overlap_characters,
                minimum_chunk_characters=self.config.minimum_chunk_characters,
            )
            for local_start, local_end in child_ranges:
                abs_start = parent_start + local_start
                abs_end = parent_start + local_end
                child_raw = raw[abs_start:abs_end]
                if not child_raw.strip():
                    continue
                # Skip tiny overlap-only fragments when a larger parent exists.
                if (
                    len(child_raw.strip()) < self.config.minimum_chunk_characters
                    and len(parent_raw) > self.config.minimum_chunk_characters
                    and (local_end - local_start) < self.config.minimum_chunk_characters
                    and local_start > 0
                    and local_end < len(parent_raw)
                ):
                    continue
                child_evidence = _evidence_ids_for_range(
                    document,
                    evidence_spans,
                    page_number=page.page_number,
                    char_start=abs_start,
                    char_end=abs_end,
                    created=created_spans,
                )
                child = self._make_chunk(
                    document=document,
                    kind=ChunkKind.PAGE,
                    level=ChunkLevel.CHILD,
                    page_numbers=[page.page_number],
                    raw_text=child_raw,
                    retrieval_text=child_raw,
                    retrieval_text_kind=RetrievalTextKind.RAW_SOURCE,
                    parent_chunk_id=parent.id,
                    source_block_ids=block_ids,
                    source_table_ids=[],
                    evidence_span_ids=child_evidence,
                    heading_path=[],
                    ordinal=ordinal,
                    metadata={"char_start": abs_start, "char_end": abs_end},
                )
                if child is None:
                    continue
                chunks.append(child)
                ordinal += 1

        return chunks, ordinal

    def _chunk_heading_path(
        self,
        document: CanonicalDocument,
        page: CanonicalPage,
        evidence_spans: list[EvidenceSpan],
        *,
        ordinal: int,
        created_spans: dict[tuple[int, int, int], EvidenceSpan],
    ) -> tuple[list[RetrievalChunk], int]:
        chunks: list[RetrievalChunk] = []
        heading_path: list[str] = []
        heading_block_ids: list[str] = []
        heading_evidence: list[str] = []
        section_blocks: list[CanonicalBlock] = []

        def flush_section() -> None:
            nonlocal ordinal
            if not section_blocks:
                return
            section_chunks, ordinal = self._emit_section(
                document,
                page,
                section_blocks,
                evidence_spans,
                heading_path=list(heading_path),
                heading_block_ids=list(heading_block_ids),
                heading_evidence=list(heading_evidence),
                ordinal=ordinal,
                created_spans=created_spans,
            )
            chunks.extend(section_chunks)
            section_blocks.clear()

        for block in page.blocks:
            if block.kind is BlockKind.TABLE:
                continue
            if block.kind in _HEADING_KINDS:
                flush_section()
                if block.kind is BlockKind.TITLE:
                    heading_path = [block.raw_text]
                    heading_block_ids = [block.id]
                    heading_evidence = _evidence_ids_for_block(evidence_spans, block)
                else:
                    heading_path = [*heading_path, block.raw_text]
                    heading_block_ids = [*heading_block_ids, block.id]
                    heading_evidence = sorted(
                        set(heading_evidence + _evidence_ids_for_block(evidence_spans, block))
                    )
                continue
            if not block.raw_text.strip():
                continue
            section_blocks.append(block)

        flush_section()
        return chunks, ordinal

    def _emit_section(
        self,
        document: CanonicalDocument,
        page: CanonicalPage,
        blocks: list[CanonicalBlock],
        evidence_spans: list[EvidenceSpan],
        *,
        heading_path: list[str],
        heading_block_ids: list[str],
        heading_evidence: list[str],
        ordinal: int,
        created_spans: dict[tuple[int, int, int], EvidenceSpan],
    ) -> tuple[list[RetrievalChunk], int]:
        chunks: list[RetrievalChunk] = []
        raw_parts = [block.raw_text for block in blocks if block.raw_text.strip()]
        if not raw_parts:
            return chunks, ordinal
        section_raw = "\n\n".join(raw_parts)
        block_ids = [block.id for block in blocks]
        section_evidence = sorted(
            {
                *heading_evidence,
                *[
                    eid
                    for block in blocks
                    for eid in _evidence_ids_for_block(evidence_spans, block)
                ],
            }
        )
        if not section_evidence:
            # Derive from page offsets when blocks have ranges.
            starts = [b.char_start for b in blocks if b.char_start is not None]
            ends = [b.char_end for b in blocks if b.char_end is not None]
            if starts and ends:
                section_evidence = _evidence_ids_for_range(
                    document,
                    evidence_spans,
                    page_number=page.page_number,
                    char_start=min(starts),
                    char_end=max(ends),
                    created=created_spans,
                )
        if not section_evidence:
            return chunks, ordinal

        parent_cap = min(self.config.parent_max_characters, self.config.maximum_chunk_characters)
        parent_kind = ChunkKind.SECTION if heading_path else ChunkKind.TEXT
        if len(section_raw) <= parent_cap:
            parent_raw_segments = [section_raw]
        else:
            parent_raw_segments = [
                section_raw[s:e]
                for s, e in _split_ranges(
                    section_raw,
                    max_characters=parent_cap,
                    overlap_characters=0,
                    minimum_chunk_characters=self.config.minimum_chunk_characters,
                )
            ]

        for parent_raw in parent_raw_segments:
            if not parent_raw.strip():
                continue
            parent = self._make_chunk(
                document=document,
                kind=parent_kind,
                level=ChunkLevel.PARENT,
                page_numbers=[page.page_number],
                raw_text=parent_raw,
                retrieval_text=parent_raw,
                retrieval_text_kind=RetrievalTextKind.RAW_SOURCE,
                parent_chunk_id=None,
                source_block_ids=block_ids + heading_block_ids,
                source_table_ids=[],
                evidence_span_ids=section_evidence,
                heading_path=heading_path,
                ordinal=ordinal,
            )
            if parent is None:
                continue
            chunks.append(parent)
            ordinal += 1

            for block in blocks:
                if not block.raw_text.strip():
                    continue
                child_chunks, ordinal = self._emit_block_children(
                    document,
                    page,
                    block,
                    evidence_spans,
                    parent_id=parent.id,
                    heading_path=heading_path,
                    heading_evidence=heading_evidence,
                    ordinal=ordinal,
                    created_spans=created_spans,
                )
                chunks.extend(child_chunks)

        return chunks, ordinal

    def _emit_block_children(
        self,
        document: CanonicalDocument,
        page: CanonicalPage,
        block: CanonicalBlock,
        evidence_spans: list[EvidenceSpan],
        *,
        parent_id: str,
        heading_path: list[str],
        heading_evidence: list[str],
        ordinal: int,
        created_spans: dict[tuple[int, int, int], EvidenceSpan],
    ) -> tuple[list[RetrievalChunk], int]:
        chunks: list[RetrievalChunk] = []
        block_evidence = sorted(
            set(_evidence_ids_for_block(evidence_spans, block) + heading_evidence)
        )
        if not block_evidence and block.char_start is not None and block.char_end is not None:
            block_evidence = _evidence_ids_for_range(
                document,
                evidence_spans,
                page_number=page.page_number,
                char_start=block.char_start,
                char_end=block.char_end,
                created=created_spans,
            )
        if not block_evidence:
            return chunks, ordinal

        child_cap = min(self.config.child_max_characters, self.config.maximum_chunk_characters)
        ranges = _split_ranges(
            block.raw_text,
            max_characters=child_cap,
            overlap_characters=self.config.child_overlap_characters,
            minimum_chunk_characters=self.config.minimum_chunk_characters,
        )
        for start, end in ranges:
            child_raw = block.raw_text[start:end]
            if not child_raw.strip():
                continue
            if heading_path:
                heading_prefix = "\n".join(heading_path)
                retrieval = f"{heading_prefix}\n{child_raw}"
                retrieval_kind = RetrievalTextKind.CONTEXT_ENRICHED
            else:
                retrieval = child_raw
                retrieval_kind = RetrievalTextKind.RAW_SOURCE
            child = self._make_chunk(
                document=document,
                kind=ChunkKind.TEXT,
                level=ChunkLevel.CHILD,
                page_numbers=[page.page_number],
                raw_text=child_raw,
                retrieval_text=retrieval,
                retrieval_text_kind=retrieval_kind,
                parent_chunk_id=parent_id,
                source_block_ids=[block.id],
                source_table_ids=[],
                evidence_span_ids=block_evidence,
                heading_path=heading_path,
                ordinal=ordinal,
                metadata={
                    "block_char_start": start,
                    "block_char_end": end,
                    "heading_context_retrieval_only": bool(heading_path),
                },
            )
            if child is None:
                continue
            chunks.append(child)
            ordinal += 1
        return chunks, ordinal

    def _chunk_table(
        self,
        document: CanonicalDocument,
        table: CanonicalTable,
        evidence_spans: list[EvidenceSpan],
        *,
        ordinal: int,
    ) -> tuple[list[RetrievalChunk], int]:
        chunks: list[RetrievalChunk] = []
        raw = table_raw_text(table)
        if not raw.strip():
            return chunks, ordinal
        table_evidence = _evidence_ids_for_table(evidence_spans, table)
        if not table_evidence:
            return chunks, ordinal

        synthetic = serialize_table(table)
        parent = self._make_chunk(
            document=document,
            kind=ChunkKind.TABLE,
            level=ChunkLevel.PARENT,
            page_numbers=list(table.page_numbers),
            raw_text=raw,
            retrieval_text=synthetic,
            retrieval_text_kind=RetrievalTextKind.SYNTHETIC_TABLE_SERIALIZATION,
            parent_chunk_id=None,
            source_block_ids=[],
            source_table_ids=[table.id],
            evidence_span_ids=table_evidence,
            heading_path=[],
            ordinal=ordinal,
            metadata={"synthetic_not_primary_evidence": True},
        )
        if parent is None:
            return chunks, ordinal
        chunks.append(parent)
        ordinal += 1

        row_indices = sorted({cell.row_index for cell in table.cells})
        for row_index in row_indices:
            row_cells = [c for c in table.cells if c.row_index == row_index]
            row_raw = "\n".join(c.raw_text for c in row_cells if c.raw_text.strip())
            if not row_raw.strip():
                continue
            row_evidence = _evidence_ids_for_row(evidence_spans, table, row_index)
            if not row_evidence:
                continue
            row_retrieval = serialize_row(table, row_index, include_headers=True)
            row_chunk = self._make_chunk(
                document=document,
                kind=ChunkKind.TABLE_ROW,
                level=ChunkLevel.CHILD,
                page_numbers=list(table.page_numbers),
                raw_text=row_raw,
                retrieval_text=row_retrieval,
                retrieval_text_kind=RetrievalTextKind.SYNTHETIC_TABLE_SERIALIZATION,
                parent_chunk_id=parent.id,
                source_block_ids=[],
                source_table_ids=[table.id],
                evidence_span_ids=row_evidence,
                heading_path=[],
                ordinal=ordinal,
                metadata={
                    "row_index": row_index,
                    "synthetic_not_primary_evidence": True,
                },
            )
            if row_chunk is None:
                continue
            chunks.append(row_chunk)
            ordinal += 1

            if not self.config.emit_table_cell_atomics:
                continue
            for cell in sorted(row_cells, key=lambda c: (c.column_index, c.id)):
                if not cell.raw_text.strip():
                    continue
                cell_evidence = _evidence_ids_for_cell(
                    evidence_spans,
                    table.id,
                    cell.row_index,
                    cell.column_index,
                )
                if not cell_evidence:
                    continue
                cell_chunk = self._make_chunk(
                    document=document,
                    kind=ChunkKind.TABLE_CELL,
                    level=ChunkLevel.ATOMIC,
                    page_numbers=[cell.page_number],
                    raw_text=cell.raw_text,
                    retrieval_text=cell.raw_text,
                    retrieval_text_kind=RetrievalTextKind.RAW_SOURCE,
                    parent_chunk_id=row_chunk.id,
                    source_block_ids=[],
                    source_table_ids=[table.id],
                    evidence_span_ids=cell_evidence,
                    heading_path=[],
                    ordinal=ordinal,
                    metadata={
                        "row_index": cell.row_index,
                        "column_index": cell.column_index,
                    },
                )
                if cell_chunk is None:
                    continue
                chunks.append(cell_chunk)
                ordinal += 1

        return chunks, ordinal
