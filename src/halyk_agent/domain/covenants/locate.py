"""Locate covenant clause text and build clause-local evidence spans."""

from __future__ import annotations

import re
from dataclasses import dataclass

from halyk_agent.domain.evidence import EvidenceSpan
from halyk_agent.domain.parsing import CanonicalDocument
from halyk_agent.domain.routing.evidence import create_identity_span


@dataclass(frozen=True, slots=True)
class LocatedClause:
    clause_id: str
    page_number: int
    char_start: int
    char_end: int
    text: str
    span: EvidenceSpan | None
    global_start: int
    global_end: int
    joined_text: str
    cursors: tuple[_DocCursor, ...]


@dataclass(frozen=True, slots=True)
class _DocCursor:
    page_number: int
    page_start: int
    page_text: str


_KEYWORDS = (
    "обязуется",
    "не допускать",
    "не менее",
    "не более",
    "превыш",
    "отношение",
    "выруч",
    "капит",
    "связанн",
    "ratio",
    "must",
    "2025-",
    "$",
    "x",
    "платеж",
    "payment",
)


def _join_document(document: CanonicalDocument) -> tuple[str, tuple[_DocCursor, ...]]:
    parts: list[str] = []
    cursors: list[_DocCursor] = []
    offset = 0
    for page in sorted(document.pages, key=lambda item: item.page_number):
        text = page.raw_text or ""
        cursors.append(_DocCursor(page_number=page.page_number, page_start=offset, page_text=text))
        parts.append(text)
        offset += len(text) + 1
    return "\n".join(parts), tuple(cursors)


def _map_global_to_page(cursors: tuple[_DocCursor, ...], global_offset: int) -> tuple[int, int]:
    for cursor in cursors:
        page_end = cursor.page_start + len(cursor.page_text)
        if cursor.page_start <= global_offset <= page_end:
            return cursor.page_number, global_offset - cursor.page_start
    last = cursors[-1]
    return last.page_number, max(0, len(last.page_text) - 1)


def _is_ws(ch: str) -> bool:
    return ch.isspace() or ch == "\xa0"


def build_ws_map(raw: str) -> tuple[str, list[int]]:
    """Return whitespace-normalized text and map normalized index → raw index."""
    norm_chars: list[str] = []
    raw_index_for_norm: list[int] = []
    i = 0
    n = len(raw)
    while i < n:
        if _is_ws(raw[i]):
            while i < n and _is_ws(raw[i]):
                i += 1
            if norm_chars and norm_chars[-1] != " ":
                norm_chars.append(" ")
                raw_index_for_norm.append(i - 1 if i > 0 else 0)
            continue
        norm_chars.append(raw[i])
        raw_index_for_norm.append(i)
        i += 1
    return "".join(norm_chars), raw_index_for_norm


def locate_clause(
    document: CanonicalDocument,
    *,
    clause_id: str,
) -> LocatedClause | None:
    """Locate body clause text across pages (Пункт 6.x)."""
    if not document.pages:
        return None
    joined, cursors = _join_document(document)
    pat = re.compile(
        rf"(?is)Пункт\s+{re.escape(clause_id)}\b|пункт\s+{re.escape(clause_id)}\b|"
        rf"Clause\s+{re.escape(clause_id)}\b"
    )
    end_pat = re.compile(r"(?is)\n\s*Пункт\s+6\.\d|\n\s*Статья\s+\d+|\n\s*Article\s+\d+")
    candidates: list[tuple[int, int, int, str]] = []
    for match in pat.finditer(joined):
        start = match.start()
        rest = joined[start + 8 :]
        end_m = end_pat.search(rest)
        end = start + 8 + (end_m.start() if end_m else min(len(rest), 2200))
        snip = joined[start:end].strip()
        score = sum(1 for kw in _KEYWORDS if kw in snip.casefold())
        if len(snip) < 80:
            score -= 5
        candidates.append((score, start, end, snip))
    if not candidates:
        return None
    candidates.sort(key=lambda row: (-row[0], row[1]))
    score, start, end, snip = candidates[0]
    if score <= 0:
        return None

    # Trim trailing whitespace from global end while keeping exact raw slice for snip.
    while end > start and _is_ws(joined[end - 1]):
        end -= 1
    clause_raw = joined[start:end]
    page_number, page_char_start = _map_global_to_page(cursors, start)
    page_cursor = next(c for c in cursors if c.page_number == page_number)
    head_end = min(page_char_start + min(180, len(clause_raw)), len(page_cursor.page_text))
    if head_end <= page_char_start:
        head_end = min(page_char_start + 1, len(page_cursor.page_text))
    span_result = create_identity_span(
        document,
        page_number=page_number,
        char_start=page_char_start,
        char_end=head_end,
    )
    return LocatedClause(
        clause_id=clause_id,
        page_number=page_number,
        char_start=page_char_start,
        char_end=head_end,
        text=clause_raw,
        span=span_result.span,
        global_start=start,
        global_end=end,
        joined_text=joined,
        cursors=cursors,
    )


def find_in_clause(
    document: CanonicalDocument,
    located: LocatedClause,
    *,
    needle: str,
) -> EvidenceSpan | None:
    """
    Locate ``needle`` strictly inside the located covenant clause region.

    Supports whitespace-normalized matching with exact raw quote recovery.
    Never searches outside the clause.
    """
    if not needle:
        return None
    clause_raw = located.joined_text[located.global_start : located.global_end]
    # Exact raw match first.
    local = clause_raw.find(needle)
    if local < 0:
        # Case-insensitive exact-length match on raw (same length only).
        low_clause = clause_raw.casefold()
        low_needle = needle.casefold()
        local = low_clause.find(low_needle)
        if local >= 0:
            needle = clause_raw[local : local + len(needle)]
    if local < 0:
        # Whitespace-normalized match with offset map.
        norm_clause, idx_map = build_ws_map(clause_raw)
        norm_needle, _ = build_ws_map(needle)
        if not norm_needle:
            return None
        pos = norm_clause.casefold().find(norm_needle.casefold())
        if pos < 0:
            return None
        raw_start = idx_map[pos]
        raw_end_idx = idx_map[pos + len(norm_needle) - 1]
        # Extend raw_end to cover full last character.
        raw_end = raw_end_idx + 1
        local = raw_start
        needle = clause_raw[raw_start:raw_end]
    abs_global = located.global_start + local
    page_number, page_start = _map_global_to_page(located.cursors, abs_global)
    page_end_global = abs_global + len(needle)
    # If the match stays on one page, emit one span; else emit first-page portion only
    # for single-span callers (multi-page builders use page_spans_for_clause_range).
    page_cursor = next(c for c in located.cursors if c.page_number == page_number)
    page_limit = page_cursor.page_start + len(page_cursor.page_text)
    char_start = page_start
    if page_end_global <= page_limit:
        char_end = page_start + len(needle)
    else:
        char_end = len(page_cursor.page_text)
    if char_end <= char_start:
        return None
    result = create_identity_span(
        document,
        page_number=page_number,
        char_start=char_start,
        char_end=char_end,
    )
    return result.span


def page_spans_for_global_range(
    document: CanonicalDocument,
    located: LocatedClause,
    *,
    global_start: int,
    global_end: int,
) -> tuple[EvidenceSpan, ...]:
    """Emit one EvidenceSpan per page overlapping [global_start, global_end)."""
    if global_end <= global_start:
        return ()
    # Clamp to clause.
    global_start = max(global_start, located.global_start)
    global_end = min(global_end, located.global_end)
    out: list[EvidenceSpan] = []
    for cursor in located.cursors:
        page_end = cursor.page_start + len(cursor.page_text)
        overlap_start = max(global_start, cursor.page_start)
        overlap_end = min(global_end, page_end)
        if overlap_end <= overlap_start:
            continue
        char_start = overlap_start - cursor.page_start
        char_end = overlap_end - cursor.page_start
        result = create_identity_span(
            document,
            page_number=cursor.page_number,
            char_start=char_start,
            char_end=char_end,
        )
        if result.span is not None:
            out.append(result.span)
    return tuple(out)


def formula_region_spans(
    document: CanonicalDocument,
    located: LocatedClause,
) -> tuple[EvidenceSpan, ...]:
    """Evidence covering the full located clause body used for formula parsing."""
    return page_spans_for_global_range(
        document,
        located,
        global_start=located.global_start,
        global_end=located.global_end,
    )
