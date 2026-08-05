"""Map Docling document objects into canonical domain models."""

from __future__ import annotations

from typing import Any

from halyk_agent.adapters.parsing.text_normalization import normalize_text
from halyk_agent.domain.parsing import (
    BlockKind,
    CanonicalBlock,
    CanonicalBoundingBox,
    CanonicalPage,
    CanonicalTable,
    CanonicalTableCell,
    CoordinateOrigin,
    ParserKind,
    ParseWarning,
    ParseWarningCode,
    block_identity,
    table_cell_identity,
    table_identity,
)


def convert_bbox_to_top_left(
    *,
    left: float,
    top: float,
    right: float,
    bottom: float,
    page_width: float,
    page_height: float,
    origin: str,
) -> CanonicalBoundingBox | None:
    """Convert Docling-style bbox into canonical TOP_LEFT coordinates.

    Docling uses ``TOPLEFT`` / ``BOTTOMLEFT``. For BOTTOMLEFT, ``t`` is the
    distance from the bottom edge upward; convert using page height.
    """
    origin_norm = origin.upper().replace("-", "").replace("_", "")
    if origin_norm in {"BOTTOMLEFT", "BOTTOM_LEFT"}:
        # Docling BOTTOMLEFT: t/b measured from bottom; convert to top-left.
        new_top = page_height - max(top, bottom)
        new_bottom = page_height - min(top, bottom)
        top, bottom = new_top, new_bottom
    elif origin_norm not in {"TOPLEFT", "TOP_LEFT"}:
        return None
    try:
        return CanonicalBoundingBox(
            left=min(left, right),
            top=min(top, bottom),
            right=max(left, right),
            bottom=max(top, bottom),
            page_width=page_width,
            page_height=page_height,
            origin=CoordinateOrigin.TOP_LEFT,
        )
    except ValueError:
        return None


def map_docling_label_to_block_kind(label: str | None) -> BlockKind:
    """Map Docling labels to justified block kinds only."""
    if not label:
        return BlockKind.UNKNOWN
    normalized = label.lower()
    mapping = {
        "title": BlockKind.TITLE,
        "section_header": BlockKind.HEADING,
        "paragraph": BlockKind.PARAGRAPH,
        "text": BlockKind.PARAGRAPH,
        "list_item": BlockKind.LIST_ITEM,
        "caption": BlockKind.CAPTION,
        "page_header": BlockKind.HEADER,
        "page_footer": BlockKind.FOOTER,
        "table": BlockKind.TABLE,
    }
    return mapping.get(normalized, BlockKind.UNKNOWN)


def _page_size(docling_doc: Any, page_no: int) -> tuple[float | None, float | None]:
    pages = getattr(docling_doc, "pages", None) or {}
    page = pages.get(page_no) if isinstance(pages, dict) else None
    if page is None:
        return None, None
    size = getattr(page, "size", None)
    if size is None:
        return None, None
    width = float(getattr(size, "width", 0) or 0)
    height = float(getattr(size, "height", 0) or 0)
    if width <= 0 or height <= 0:
        return None, None
    return width, height


def _prov_page_and_bbox(
    item: Any,
    *,
    page_width: float | None,
    page_height: float | None,
    warnings: list[ParseWarning],
    source_item_id: str | None,
) -> tuple[int | None, CanonicalBoundingBox | None]:
    prov = getattr(item, "prov", None) or []
    if not prov:
        warnings.append(
            ParseWarning(
                code=ParseWarningCode.MISSING_PROVENANCE,
                message="missing provenance; bbox omitted",
                source_item_id=source_item_id,
            )
        )
        return None, None
    first = prov[0]
    page_no = getattr(first, "page_no", None)
    bbox_obj = getattr(first, "bbox", None)
    if page_no is None:
        return None, None
    page_number = int(page_no)
    if bbox_obj is None or page_width is None or page_height is None:
        if bbox_obj is not None and (page_width is None or page_height is None):
            warnings.append(
                ParseWarning(
                    code=ParseWarningCode.MISSING_PROVENANCE,
                    message="bbox skipped because page dimensions are absent",
                    page_number=page_number,
                    source_item_id=source_item_id,
                )
            )
        return page_number, None
    origin = str(getattr(bbox_obj, "coord_origin", "TOPLEFT"))
    # Prefer Docling's conversion helper when available.
    try:
        if hasattr(bbox_obj, "to_top_left_origin"):
            bbox_obj = bbox_obj.to_top_left_origin(page_height)
            origin = "TOPLEFT"
    except Exception:
        pass
    left = float(getattr(bbox_obj, "l", getattr(bbox_obj, "left", 0.0)))
    top = float(getattr(bbox_obj, "t", getattr(bbox_obj, "top", 0.0)))
    right = float(getattr(bbox_obj, "r", getattr(bbox_obj, "right", 0.0)))
    bottom = float(getattr(bbox_obj, "b", getattr(bbox_obj, "bottom", 0.0)))
    bbox = convert_bbox_to_top_left(
        left=left,
        top=top,
        right=right,
        bottom=bottom,
        page_width=page_width,
        page_height=page_height,
        origin=origin,
    )
    if bbox is None:
        warnings.append(
            ParseWarning(
                code=ParseWarningCode.INVALID_BBOX,
                message="invalid provenance bbox omitted",
                page_number=page_number,
                source_item_id=source_item_id,
            )
        )
    return page_number, bbox


def map_docling_document(
    docling_doc: Any,
    *,
    document_id: str,
) -> tuple[list[CanonicalPage], list[ParseWarning]]:
    """Map a DoclingDocument into canonical pages/blocks/tables.

    Uses native items (not Markdown reparse).
    """
    warnings: list[ParseWarning] = []
    page_texts: dict[int, list[str]] = {}
    page_blocks: dict[int, list[CanonicalBlock]] = {}
    page_tables: dict[int, list[CanonicalTable]] = {}
    page_sizes: dict[int, tuple[float | None, float | None]] = {}

    # Discover page numbers from doc.pages if present.
    pages_attr = getattr(docling_doc, "pages", None) or {}
    if isinstance(pages_attr, dict):
        for page_no in pages_attr:
            page_sizes[int(page_no)] = _page_size(docling_doc, int(page_no))
            page_texts.setdefault(int(page_no), [])
            page_blocks.setdefault(int(page_no), [])
            page_tables.setdefault(int(page_no), [])

    texts = list(getattr(docling_doc, "texts", None) or [])
    for ordinal_global, item in enumerate(texts):
        source_item_id = str(getattr(item, "self_ref", None) or f"text:{ordinal_global}")
        label = getattr(getattr(item, "label", None), "value", None) or str(
            getattr(item, "label", "") or ""
        )
        raw_text = str(getattr(item, "text", "") or "")
        # Provisional page lookup for size
        prov = getattr(item, "prov", None) or []
        provisional_page = int(getattr(prov[0], "page_no", 1)) if prov else 1
        width, height = page_sizes.get(provisional_page) or _page_size(
            docling_doc, provisional_page
        )
        page_sizes[provisional_page] = (width, height)
        page_number, bbox = _prov_page_and_bbox(
            item,
            page_width=width,
            page_height=height,
            warnings=warnings,
            source_item_id=source_item_id,
        )
        if page_number is None:
            page_number = provisional_page
        page_texts.setdefault(page_number, [])
        page_blocks.setdefault(page_number, [])
        page_tables.setdefault(page_number, [])
        # Offsets assigned after page text is assembled.
        kind = map_docling_label_to_block_kind(label)
        page_texts[page_number].append(raw_text)
        page_blocks[page_number].append(
            CanonicalBlock(
                id="pending",
                page_number=page_number,
                ordinal=len(page_blocks[page_number]),
                kind=kind,
                raw_text=raw_text,
                normalized_text=normalize_text(raw_text),
                char_start=None,
                char_end=None,
                bbox=bbox,
                source_parser=ParserKind.DOCLING,
                source_item_id=source_item_id,
            )
        )

    tables = list(getattr(docling_doc, "tables", None) or [])
    for table_ordinal, table in enumerate(tables):
        source_item_id = str(getattr(table, "self_ref", None) or f"table:{table_ordinal}")
        prov = getattr(table, "prov", None) or []
        provisional_page = int(getattr(prov[0], "page_no", 1)) if prov else 1
        width, height = page_sizes.get(provisional_page) or _page_size(
            docling_doc, provisional_page
        )
        page_sizes[provisional_page] = (width, height)
        page_number, bbox = _prov_page_and_bbox(
            table,
            page_width=width,
            page_height=height,
            warnings=warnings,
            source_item_id=source_item_id,
        )
        if page_number is None:
            page_number = provisional_page
        page_numbers = [page_number]
        table_id = table_identity(
            document_id,
            table_ordinal,
            page_numbers,
            source_item_id,
        )
        cells: list[CanonicalTableCell] = []
        data = getattr(table, "data", None)
        grid = getattr(data, "grid", None) if data is not None else None
        row_count = 0
        column_count = 0
        if grid:
            row_count = len(grid)
            column_count = max((len(row) for row in grid), default=0)
            for r_idx, row in enumerate(grid):
                for c_idx, cell in enumerate(row):
                    cell_text = str(getattr(cell, "text", "") or "")
                    row_span = int(getattr(cell, "row_span", 1) or 1)
                    col_span = int(getattr(cell, "col_span", 1) or 1)
                    cell_id = table_cell_identity(
                        table_id,
                        r_idx,
                        c_idx,
                        row_span,
                        col_span,
                        cell_text,
                    )
                    cells.append(
                        CanonicalTableCell(
                            id=cell_id,
                            page_number=page_number,
                            table_id=table_id,
                            row_index=r_idx,
                            column_index=c_idx,
                            row_span=max(1, row_span),
                            column_span=max(1, col_span),
                            raw_text=cell_text,
                            normalized_text=normalize_text(cell_text),
                            bbox=None,
                            source_item_id=str(
                                getattr(cell, "self_ref", None)
                                or f"{source_item_id}:{r_idx}:{c_idx}"
                            ),
                        )
                    )
        caption = None
        cap = getattr(table, "caption_text", None)
        if callable(cap):
            try:
                caption = str(cap(doc=docling_doc) or "") or None
            except Exception:
                caption = None
        page_tables.setdefault(page_number, [])
        page_tables[page_number].append(
            CanonicalTable(
                id=table_id,
                page_numbers=page_numbers,
                ordinal=len(page_tables[page_number]),
                cells=cells,
                row_count=row_count,
                column_count=column_count,
                bbox=bbox,
                caption=caption,
                source_item_id=source_item_id,
            )
        )

    # Ensure at least page 1 exists for empty docs.
    if not page_texts and not page_tables:
        page_texts[1] = []
        page_blocks[1] = []
        page_tables[1] = []
        page_sizes[1] = (None, None)

    canonical_pages: list[CanonicalPage] = []
    for page_number in sorted(set(page_texts) | set(page_tables) | set(page_blocks)):
        page_texts.get(page_number, [])
        # Rebuild page text with separators matching block offsets.
        raw_parts: list[str] = []
        finalized_blocks: list[CanonicalBlock] = []
        cursor = 0
        for ordinal, block in enumerate(page_blocks.get(page_number, [])):
            if ordinal > 0:
                raw_parts.append("\n")
                cursor += 1
            start = cursor
            raw_parts.append(block.raw_text)
            cursor += len(block.raw_text)
            end = cursor
            block_id = block_identity(
                document_id,
                page_number,
                ordinal,
                block.kind,
                block.raw_text,
                block.bbox,
            )
            finalized_blocks.append(
                CanonicalBlock(
                    id=block_id,
                    page_number=page_number,
                    ordinal=ordinal,
                    kind=block.kind,
                    raw_text=block.raw_text,
                    normalized_text=block.normalized_text,
                    char_start=start,
                    char_end=end,
                    bbox=block.bbox,
                    source_parser=ParserKind.DOCLING,
                    source_item_id=block.source_item_id,
                    metadata=block.metadata,
                )
            )
        raw_text = "".join(raw_parts)
        width, height = page_sizes.get(page_number, (None, None))
        # Drop bboxes if dimensions missing.
        if width is None or height is None:
            finalized_blocks = [
                CanonicalBlock(
                    id=b.id,
                    page_number=b.page_number,
                    ordinal=b.ordinal,
                    kind=b.kind,
                    raw_text=b.raw_text,
                    normalized_text=b.normalized_text,
                    char_start=b.char_start,
                    char_end=b.char_end,
                    bbox=None,
                    source_parser=b.source_parser,
                    source_item_id=b.source_item_id,
                    metadata=b.metadata,
                )
                for b in finalized_blocks
            ]
            tables_for_page = [
                CanonicalTable(
                    id=t.id,
                    page_numbers=t.page_numbers,
                    ordinal=t.ordinal,
                    cells=t.cells,
                    row_count=t.row_count,
                    column_count=t.column_count,
                    bbox=None,
                    caption=t.caption,
                    source_item_id=t.source_item_id,
                )
                for t in page_tables.get(page_number, [])
            ]
        else:
            tables_for_page = page_tables.get(page_number, [])
        canonical_pages.append(
            CanonicalPage(
                page_number=page_number,
                width=width,
                height=height,
                raw_text=raw_text,
                normalized_text=normalize_text(raw_text),
                blocks=finalized_blocks,
                tables=tables_for_page,
                warnings=[],
            )
        )
    return canonical_pages, warnings
