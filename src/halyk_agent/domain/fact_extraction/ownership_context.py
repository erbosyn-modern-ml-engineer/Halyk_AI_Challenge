"""Local semantic context gate for ownership / voting-rights percentages."""

# ruff: noqa: RUF001

from __future__ import annotations

import re

from halyk_agent.domain.fact_extraction.text_normalize import cue_corpus

# Nearest matching header/section cue wins. Prefer specific phrases first in lists.
_POSITIVE_HEADER_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"доля\s+голосующих\s+прав", re.IGNORECASE),
        "OWNERSHIP_TABLE_VOTING_RIGHTS",
    ),
    (
        re.compile(r"voting\s+rights", re.IGNORECASE),
        "OWNERSHIP_TABLE_VOTING_RIGHTS",
    ),
    (
        re.compile(r"доля\s+владения", re.IGNORECASE),
        "OWNERSHIP_TABLE_OWNERSHIP_SHARE",
    ),
    (
        re.compile(r"ownership\s+interest|shareholding|equity\s+interest", re.IGNORECASE),
        "OWNERSHIP_TABLE_OWNERSHIP_SHARE",
    ),
    (
        re.compile(r"структура\s+(?:собственности|владения)", re.IGNORECASE),
        "OWNERSHIP_SECTION_STRUCTURE",
    ),
    (
        re.compile(r"бенефициарн\w*\s+владен", re.IGNORECASE),
        "OWNERSHIP_SECTION_BENEFICIAL",
    ),
    (
        re.compile(r"beneficial\s+ownership", re.IGNORECASE),
        "OWNERSHIP_SECTION_BENEFICIAL",
    ),
)

_NEGATIVE_HEADER_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"доля\s+активов\s+в\s+залоге", re.IGNORECASE),
        "NON_OWNERSHIP_PLEDGED_ASSETS",
    ),
    (
        re.compile(r"активов?\s+в\s+залоге", re.IGNORECASE),
        "NON_OWNERSHIP_PLEDGED_ASSETS",
    ),
    (
        re.compile(r"pledged\s+assets|share\s+of\s+assets\s+pledged", re.IGNORECASE),
        "NON_OWNERSHIP_PLEDGED_ASSETS",
    ),
    (
        re.compile(r"обеспечительн\w*\s+покрыт", re.IGNORECASE),
        "NON_OWNERSHIP_COLLATERAL_COVERAGE",
    ),
    (
        re.compile(r"collateral\s+coverage|security\s+coverage", re.IGNORECASE),
        "NON_OWNERSHIP_COLLATERAL_COVERAGE",
    ),
    (
        re.compile(r"периметр\s+обеспечения", re.IGNORECASE),
        "NON_OWNERSHIP_COLLATERAL_COVERAGE",
    ),
    (
        re.compile(r"\bзалог\b", re.IGNORECASE),
        "NON_OWNERSHIP_PLEDGED_ASSETS",
    ),
    (
        re.compile(r"\bcollateral\b", re.IGNORECASE),
        "NON_OWNERSHIP_COLLATERAL_COVERAGE",
    ),
)

_CONTEXT_CHARS = 700


def _last_match_end(pattern: re.Pattern[str], text: str) -> int:
    end = -1
    for match in pattern.finditer(text):
        end = match.end()
    return end


def ownership_context_reason(page_text: str, row_start: int) -> str | None:
    """
    Return a positive ownership reason code if the nearest local table/section
    header means ownership/voting rights; otherwise None.

    Uses cue_corpus so mojibake-normalized headers are visible. Context is local
    to the text preceding the row — not document-wide.
    """
    if row_start < 0:
        row_start = 0
    window_raw = page_text[max(0, row_start - _CONTEXT_CHARS) : row_start]
    window = cue_corpus(window_raw)
    if not window.strip():
        return None

    best_pos_end = -1
    best_pos_reason: str | None = None
    for pattern, reason in _POSITIVE_HEADER_PATTERNS:
        end = _last_match_end(pattern, window)
        if end > best_pos_end:
            best_pos_end = end
            best_pos_reason = reason

    best_neg_end = -1
    for pattern, _reason in _NEGATIVE_HEADER_PATTERNS:
        end = _last_match_end(pattern, window)
        if end > best_neg_end:
            best_neg_end = end

    # Nearest semantic header wins. Negative after positive → reject.
    if best_neg_end > best_pos_end:
        return None
    if best_pos_end >= 0 and best_pos_reason is not None:
        return best_pos_reason
    return None


def is_ownership_percentage_context(page_text: str, row_start: int) -> bool:
    """True when the row's local preceding context is ownership/voting rights."""
    return ownership_context_reason(page_text, row_start) is not None
