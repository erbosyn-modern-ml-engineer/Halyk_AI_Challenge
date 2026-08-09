"""Locate covenant clause text and build clause-local evidence spans."""

# ruff: noqa: RUF001

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


# These are intentionally semantic rather than dataset/year/currency specific.
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
    "ковенант",
    "ratio",
    "must",
    "shall",
    "covenant",
    "payment",
    "платеж",
    "revenue",
    "ebitda",
    "capex",
)

_HEADING_RE = re.compile(
    r"(?im)^[ \t]*(?:Пункт|Clause|Article|Статья|Тармақ)\s+"
    r"(?P<id>\d+(?:\.\d+)*)\b[^\n]*"
)


def join_document_text(document: CanonicalDocument) -> tuple[str, tuple[_DocCursor, ...]]:
    """Join page texts with newlines; return text and page cursors."""
    parts: list[str] = []
    cursors: list[_DocCursor] = []
    offset = 0
    for page in sorted(document.pages, key=lambda item: item.page_number):
        text = page.raw_text or ""
        cursors.append(_DocCursor(page_number=page.page_number, page_start=offset, page_text=text))
        parts.append(text)
        offset += len(text) + 1
    return "\n".join(parts), tuple(cursors)


_join_document = join_document_text


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
    """Return whitespace-normalized text and map normalized index to raw index."""
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


def _candidate_score(text: str) -> int:
    lowered = text.casefold()
    score = sum(1 for keyword in _KEYWORDS if keyword in lowered)
    if re.search(r"(?:<=|>=|≤|≥|<|>|\bне\s+(?:более|менее)\b|\bat\s+(?:most|least)\b)", text, re.I):
        score += 1
    if re.search(r"\d+(?:[.,]\d+)?\s*(?:%|x\b|[A-Z]{3}\b|₸|€|£|\$)", text):
        score += 1
    if len(text.strip()) < 40:
        score -= 4
    return score


def locate_clause(
    document: CanonicalDocument,
    *,
    clause_id: str,
) -> LocatedClause | None:
    """Locate an exact structural clause and stop at the next structural heading."""
    if not document.pages:
        return None
    joined, cursors = _join_document(document)
    headings = list(_HEADING_RE.finditer(joined))
    candidates: list[tuple[int, int, int]] = []
    for index, heading in enumerate(headings):
        if heading.group("id") != clause_id:
            continue
        start = heading.start()
        structural_end = headings[index + 1].start() if index + 1 < len(headings) else len(joined)
        # Long annexes sometimes have no recognizable next heading. Keep a bounded
        # window rather than allowing one clause to consume the rest of a document.
        end = min(structural_end, start + 5000)
        candidates.append((_candidate_score(joined[start:end]), start, end))

    # Some extracted PDFs lose the word before the clause number. Allow a bare
    # heading only when it starts a line and still apply the same next-heading bound.
    if not candidates:
        bare = re.compile(rf"(?im)^[ \t]*{re.escape(clause_id)}\b[^\n]*")
        for match in bare.finditer(joined):
            next_heading = _HEADING_RE.search(joined, match.end())
            end = min(next_heading.start() if next_heading else len(joined), match.start() + 5000)
            candidates.append((_candidate_score(joined[match.start() : end]), match.start(), end))

    if not candidates:
        return None
    candidates.sort(key=lambda row: (-row[0], row[1]))
    score, start, end = candidates[0]
    if score <= 0:
        return None

    while end > start and _is_ws(joined[end - 1]):
        end -= 1
    clause_raw = joined[start:end]
    page_number, page_char_start = _map_global_to_page(cursors, start)
    page_cursor = next(cursor for cursor in cursors if cursor.page_number == page_number)
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
    """Locate a quote strictly inside the selected clause."""
    if not needle:
        return None
    clause_raw = located.joined_text[located.global_start : located.global_end]
    local = clause_raw.find(needle)
    if local < 0:
        low_clause = clause_raw.casefold()
        low_needle = needle.casefold()
        local = low_clause.find(low_needle)
        if local >= 0:
            needle = clause_raw[local : local + len(needle)]
    if local < 0:
        norm_clause, idx_map = build_ws_map(clause_raw)
        norm_needle, _ = build_ws_map(needle)
        if not norm_needle:
            return None
        pos = norm_clause.casefold().find(norm_needle.casefold())
        if pos < 0:
            return None
        raw_start = idx_map[pos]
        raw_end = idx_map[pos + len(norm_needle) - 1] + 1
        local = raw_start
        needle = clause_raw[raw_start:raw_end]
    abs_global = located.global_start + local
    page_number, page_start = _map_global_to_page(located.cursors, abs_global)
    page_end_global = abs_global + len(needle)
    page_cursor = next(cursor for cursor in located.cursors if cursor.page_number == page_number)
    page_limit = page_cursor.page_start + len(page_cursor.page_text)
    char_start = page_start
    char_end = (
        page_start + len(needle) if page_end_global <= page_limit else len(page_cursor.page_text)
    )
    if char_end <= char_start:
        return None
    return create_identity_span(
        document,
        page_number=page_number,
        char_start=char_start,
        char_end=char_end,
    ).span


def page_spans_for_global_range(
    document: CanonicalDocument,
    located: LocatedClause,
    *,
    global_start: int,
    global_end: int,
) -> tuple[EvidenceSpan, ...]:
    """Emit one EvidenceSpan per page overlapping a global range."""
    if global_end <= global_start:
        return ()
    global_start = max(global_start, located.global_start)
    global_end = min(global_end, located.global_end)
    out: list[EvidenceSpan] = []
    for cursor in located.cursors:
        page_end = cursor.page_start + len(cursor.page_text)
        overlap_start = max(global_start, cursor.page_start)
        overlap_end = min(global_end, page_end)
        if overlap_end <= overlap_start:
            continue
        result = create_identity_span(
            document,
            page_number=cursor.page_number,
            char_start=overlap_start - cursor.page_start,
            char_end=overlap_end - cursor.page_start,
        )
        if result.span is not None:
            out.append(result.span)
    return tuple(out)


def formula_region_spans(
    document: CanonicalDocument,
    located: LocatedClause,
) -> tuple[EvidenceSpan, ...]:
    """Evidence covering the full clause body used for formula parsing."""
    return page_spans_for_global_range(
        document,
        located,
        global_start=located.global_start,
        global_end=located.global_end,
    )


def find_quote_in_document(
    document: CanonicalDocument,
    *,
    needle: str,
) -> EvidenceSpan | None:
    """Locate a quote anywhere in the document; first page hit wins."""
    if not needle:
        return None
    joined, cursors = _join_document(document)
    local = joined.find(needle)
    if local < 0:
        local = joined.casefold().find(needle.casefold())
        if local >= 0:
            needle = joined[local : local + len(needle)]
    if local < 0:
        norm_joined, idx_map = build_ws_map(joined)
        norm_needle, _ = build_ws_map(needle)
        if not norm_needle:
            return None
        pos = norm_joined.casefold().find(norm_needle.casefold())
        if pos < 0:
            return None
        raw_start = idx_map[pos]
        raw_end = idx_map[pos + len(norm_needle) - 1] + 1
        local = raw_start
        needle = joined[raw_start:raw_end]
    page_number, page_start = _map_global_to_page(cursors, local)
    page_cursor = next(cursor for cursor in cursors if cursor.page_number == page_number)
    page_limit = page_cursor.page_start + len(page_cursor.page_text)
    page_end_global = local + len(needle)
    char_end = page_start + len(needle) if page_end_global <= page_limit else len(page_cursor.page_text)
    if char_end <= page_start:
        return None
    return create_identity_span(
        document,
        page_number=page_number,
        char_start=page_start,
        char_end=char_end,
    ).span
