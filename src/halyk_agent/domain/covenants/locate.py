"""Locate covenant clause text and build evidence spans."""

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


@dataclass(frozen=True, slots=True)
class _DocCursor:
    page_number: int
    page_start: int  # global offset of page start in joined text
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
        offset += len(text) + 1  # newline joiner
    return "\n".join(parts), tuple(cursors)


def _map_global_to_page(cursors: tuple[_DocCursor, ...], global_offset: int) -> tuple[int, int]:
    for cursor in cursors:
        page_end = cursor.page_start + len(cursor.page_text)
        if cursor.page_start <= global_offset <= page_end:
            return cursor.page_number, global_offset - cursor.page_start
    last = cursors[-1]
    return last.page_number, max(0, len(last.page_text) - 1)


def _normalize_ws(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


def locate_clause(
    document: CanonicalDocument,
    *,
    clause_id: str,
) -> LocatedClause | None:
    """
    Locate body clause text across pages (Пункт 6.x), preferring semantic-rich hits.
    """
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
        # Ignore tiny TOC-like fragments.
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

    page_number, page_char_start = _map_global_to_page(cursors, start)
    # Evidence span covers the clause head on the starting page (auditable anchor).
    # Full multi-page clause text is retained in LocatedClause.text for parsing.
    head_end = min(page_char_start + min(180, len(snip)), len(cursors[0].page_text))
    # Find the correct cursor for page_number.
    page_cursor = next(c for c in cursors if c.page_number == page_number)
    head_end = min(page_char_start + min(180, len(snip)), len(page_cursor.page_text))
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
        text=snip,
        span=span_result.span,
    )


def find_subspan(
    document: CanonicalDocument,
    *,
    page_number: int,
    clause_start: int,
    clause_text: str,
    needle: str,
) -> EvidenceSpan | None:
    """
    Find needle evidence.

    Prefer an exact match on the clause-start page near clause_start; otherwise search
    the whole document for the first exact raw occurrence.
    """
    if not needle:
        return None
    page = next((p for p in document.pages if p.page_number == page_number), None)
    if page is not None and page.raw_text:
        local = page.raw_text.find(needle, max(0, clause_start - 20))
        if local < 0:
            local = page.raw_text.find(needle)
        if local >= 0:
            result = create_identity_span(
                document,
                page_number=page_number,
                char_start=local,
                char_end=local + len(needle),
            )
            return result.span

    # Cross-page fallback.
    for page in document.pages:
        text = page.raw_text or ""
        idx = text.find(needle)
        if idx < 0:
            # token fallback
            token = needle.strip().split()[0] if needle.strip() else ""
            if not token:
                continue
            idx = text.find(token)
            if idx < 0:
                continue
            needle = token
        result = create_identity_span(
            document,
            page_number=page.page_number,
            char_start=idx,
            char_end=idx + len(needle),
        )
        if result.span is not None:
            return result.span
    return None
