"""Evidence span helpers for Stage 5C taxonomy/authority."""

# ruff: noqa: RUF001

from __future__ import annotations

import re

from halyk_agent.domain.evidence import EvidenceSpan
from halyk_agent.domain.parsing import CanonicalDocument
from halyk_agent.domain.routing.evidence import IdentitySpanResult, create_identity_span

_NEGATION_WINDOW = 48
_NEGATION_RE = re.compile(
    r"(?ix)"
    r"(?:"
    r"\bnot\b(?:\s+\w+){0,3}\s+"
    r"|\bне\b(?:\s+\w+){0,3}\s+"
    r"|does\s+not\s+"
    r"|is\s+not\s+"
    r"|не\s+является\s+"
    r")"
)


def find_first_span(
    document: CanonicalDocument,
    *,
    pattern: str,
    casefold: bool = True,
) -> IdentitySpanResult:
    """
    Locate the first literal occurrence of ``pattern`` in page raw text.

    Matching is case-insensitive when ``casefold`` is true, but the EvidenceSpan
    quote is always the exact raw substring.
    """
    needle = pattern.casefold() if casefold else pattern
    for page in document.pages:
        text = page.raw_text or ""
        if not text:
            continue
        hay = text.casefold() if casefold else text
        idx = hay.find(needle)
        if idx < 0:
            continue
        raw_slice = text[idx : idx + len(pattern)]
        if casefold and raw_slice.casefold() != needle:
            end = idx
            built = ""
            while end < len(text) and built.casefold() != needle:
                built += text[end]
                end += 1
            if built.casefold() != needle:
                continue
        else:
            end = idx + len(raw_slice)
        return create_identity_span(
            document,
            page_number=page.page_number,
            char_start=idx,
            char_end=end,
        )
    return IdentitySpanResult(span=None)


def require_span_or_none(
    document: CanonicalDocument,
    *,
    patterns: tuple[str, ...],
) -> EvidenceSpan | None:
    for pattern in patterns:
        result = find_first_span(document, pattern=pattern)
        if result.span is not None:
            return result.span
    return None


# A lifecycle *status banner* is a document-level stamp, not covenant prose.
# Sentence/line anchoring plus non-lowercase form separates a stamped status
# assertion at the head of a line from the same words used inside a sentence --
# the ELSE branch of a springing covenant ("the restriction does not apply") and
# ordinary disposal language ("obsolete or worn assets") both read as prose.
# Neither the marker vocabulary nor this test names any dataset, scenario or file.
_BANNER_ANCHOR_RE = re.compile(r"[.;!?·|)\]»\"]\s*$|^\s*$")


def is_status_banner(text: str, *, start: int, end: int) -> bool:
    """True when a marker occurrence reads as a standalone document-status stamp."""
    matched = text[start:end]
    if not matched.strip():
        return False
    # Body prose keeps markers in running lowercase; banners are stamped.
    if matched.islower():
        return False
    line_start = text.rfind("\n", 0, start) + 1
    prefix = text[line_start:start]
    return not (prefix.strip() and _BANNER_ANCHOR_RE.search(prefix) is None)


def find_status_banner_span(
    document: CanonicalDocument,
    *,
    patterns: tuple[str, ...],
) -> EvidenceSpan | None:
    """Find the first marker occurrence that reads as a document-status banner."""
    for pattern in patterns:
        needle = pattern.casefold()
        for page in document.pages:
            text = page.raw_text or ""
            if not text:
                continue
            hay = text.casefold()
            cursor = 0
            while True:
                idx = hay.find(needle, cursor)
                if idx < 0:
                    break
                end = idx + len(pattern)
                if text[idx:end].casefold() != needle:
                    cursor = idx + 1
                    continue
                if not is_status_banner(text, start=idx, end=end):
                    cursor = end
                    continue
                result = create_identity_span(
                    document,
                    page_number=page.page_number,
                    char_start=idx,
                    char_end=end,
                )
                if result.span is not None:
                    return result.span
                cursor = end
    return None


def _is_negated_context(text: str, *, start: int, end: int) -> bool:
    window = text[max(0, start - _NEGATION_WINDOW) : end + _NEGATION_WINDOW]
    return _NEGATION_RE.search(window) is not None


def find_first_span_non_negated(
    document: CanonicalDocument,
    *,
    patterns: tuple[str, ...],
) -> EvidenceSpan | None:
    """Find first pattern hit whose local window is not negated."""
    for pattern in patterns:
        needle = pattern.casefold()
        for page in document.pages:
            text = page.raw_text or ""
            if not text:
                continue
            hay = text.casefold()
            cursor = 0
            while True:
                idx = hay.find(needle, cursor)
                if idx < 0:
                    break
                end = idx + len(pattern)
                raw_slice = text[idx:end]
                if raw_slice.casefold() != needle:
                    built_end = idx
                    built = ""
                    while built_end < len(text) and built.casefold() != needle:
                        built += text[built_end]
                        built_end += 1
                    if built.casefold() != needle:
                        cursor = idx + 1
                        continue
                    end = built_end
                if _is_negated_context(text, start=idx, end=end):
                    cursor = end
                    continue
                result = create_identity_span(
                    document,
                    page_number=page.page_number,
                    char_start=idx,
                    char_end=end,
                )
                if result.span is not None:
                    return result.span
                cursor = end
    return None
