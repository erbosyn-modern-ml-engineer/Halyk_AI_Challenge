"""Deterministic table serialization for retrieval (never primary evidence)."""

from __future__ import annotations

from halyk_agent.domain.parsing import CanonicalTable, CanonicalTableCell


def _row_cells(
    table: CanonicalTable,
    row_index: int,
) -> list[CanonicalTableCell]:
    return [cell for cell in table.cells if cell.row_index == row_index]


def detect_header_texts(table: CanonicalTable) -> list[str]:
    """Treat row 0 as header when present; otherwise empty list."""
    headers = _row_cells(table, 0)
    if not headers:
        return []
    by_col = {cell.column_index: cell.raw_text for cell in headers}
    return [by_col.get(col, "") for col in range(table.column_count)]


def serialize_row(
    table: CanonicalTable,
    row_index: int,
    *,
    include_headers: bool = True,
) -> str:
    """Serialize one table row deterministically."""
    headers = detect_header_texts(table) if include_headers else []
    cells = _row_cells(table, row_index)
    by_col = {cell.column_index: cell.raw_text for cell in cells}
    values = [by_col.get(col, "") for col in range(table.column_count)]
    lines: list[str] = []
    if include_headers and headers and row_index > 0:
        lines.append("Columns: " + " | ".join(headers))
    lines.append(f"Row {row_index}: " + " | ".join(values))
    return "\n".join(lines)


def serialize_table(table: CanonicalTable) -> str:
    """Build deterministic synthetic retrieval text for an entire table."""
    lines: list[str] = []
    caption = (table.caption or "").strip()
    if caption:
        lines.append(f"Caption: {caption}")
    headers = detect_header_texts(table)
    if headers:
        lines.append("Columns: " + " | ".join(headers))
    row_indices = sorted({cell.row_index for cell in table.cells})
    for row_index in row_indices:
        cells = _row_cells(table, row_index)
        by_col = {cell.column_index: cell.raw_text for cell in cells}
        values = [by_col.get(col, "") for col in range(table.column_count)]
        lines.append(f"Row {row_index}: " + " | ".join(values))
    if not lines:
        lines.append(f"Table: {table.id}")
    return "\n".join(lines)


def table_raw_text(table: CanonicalTable) -> str:
    """Concatenate raw cell texts in deterministic row/column order."""
    parts: list[str] = []
    for cell in sorted(table.cells, key=lambda c: (c.row_index, c.column_index, c.id)):
        if cell.raw_text.strip():
            parts.append(cell.raw_text)
    return "\n".join(parts)
