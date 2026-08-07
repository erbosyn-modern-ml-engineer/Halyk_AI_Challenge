"""Source-text normalization helpers (mojibake repair for cue matching)."""

from __future__ import annotations

_REPAIR_MARKERS = (
    "владе",
    "связан",
    "бенефиц",
    "голосующ",
    "организац",
    "перекласс",
    "дочерн",
)


def try_repair_mojibake(text: str) -> str:
    """
    Repair common UTF-8→cp1251 mojibake when it recovers Russian financial cues.

    Returns the original text when repair is not applicable or not helpful.
    Offsets in the repaired string must NOT be used against the original page.
    """
    if not text:
        return text
    try:
        repaired = text.encode("cp1251").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text
    if repaired == text:
        return text
    original_hits = sum(1 for m in _REPAIR_MARKERS if m in text.casefold())
    repaired_hits = sum(1 for m in _REPAIR_MARKERS if m in repaired.casefold())
    if repaired_hits > original_hits:
        return repaired
    return text


def cue_corpus(text: str) -> str:
    """Return text best suited for lexical cue matching (repaired when helpful)."""
    return try_repair_mojibake(text)
