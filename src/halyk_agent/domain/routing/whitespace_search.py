"""Whitespace-normalized search with exact raw-offset provenance.

Matching collapses Unicode whitespace runs to a single ASCII space while
preserving every non-whitespace character. A hit always maps back to a
half-open raw span so that ``quote == raw_text[start:end]`` remains true
(including embedded newlines/tabs). Evidence quotes are never manufactured
from the normalized view.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WhitespaceNormalizedView:
    """Matching view of raw text with deterministic raw-offset mapping."""

    raw: str
    normalized: str
    # For each normalized character index, the raw index of that character.
    # Normalized spaces map to the first raw whitespace character of the run.
    norm_to_raw: tuple[int, ...]

    def raw_span(self, norm_start: int, norm_end: int) -> tuple[int, int]:
        """Map a half-open normalized span to a half-open raw span."""
        if norm_start < 0 or norm_end < norm_start or norm_end > len(self.normalized):
            raise ValueError("normalized span out of range")
        if norm_start == norm_end:
            if not self.norm_to_raw:
                return 0, 0
            if norm_start >= len(self.norm_to_raw):
                return len(self.raw), len(self.raw)
            pos = self.norm_to_raw[norm_start]
            return pos, pos
        raw_start = self.norm_to_raw[norm_start]
        last_raw = self.norm_to_raw[norm_end - 1]
        raw_end = last_raw + 1
        return raw_start, raw_end


def build_whitespace_normalized_view(raw: str) -> WhitespaceNormalizedView:
    """
    Build a matching view that collapses whitespace runs to one ASCII space.

    Leading and trailing whitespace runs are omitted from the normalized view.
    Internal runs become a single ``" "`` mapped to the first raw whitespace
    index of that run.
    """
    norm_chars: list[str] = []
    norm_to_raw: list[int] = []
    pending_ws_start: int | None = None

    for index, char in enumerate(raw):
        if char.isspace():
            if pending_ws_start is None:
                pending_ws_start = index
            continue
        if pending_ws_start is not None and norm_chars:
            norm_chars.append(" ")
            norm_to_raw.append(pending_ws_start)
        pending_ws_start = None
        norm_chars.append(char)
        norm_to_raw.append(index)

    return WhitespaceNormalizedView(
        raw=raw,
        normalized="".join(norm_chars),
        norm_to_raw=tuple(norm_to_raw),
    )


def normalize_needle(needle: str) -> str:
    """Whitespace-normalize a search needle (no fuzzy / punctuation changes)."""
    return build_whitespace_normalized_view(needle).normalized


def find_whitespace_normalized(
    raw: str,
    needle: str,
    *,
    start: int = 0,
) -> list[tuple[int, int]]:
    """
    Find non-overlapping needle occurrences via whitespace-normalized matching.

    Returns half-open **raw** ``(start, end)`` spans. Empty / all-whitespace
    needles yield no hits.
    """
    needle_norm = normalize_needle(needle)
    if not needle_norm:
        return []
    view = build_whitespace_normalized_view(raw)
    if not view.normalized:
        return []

    # ``start`` is a raw offset; map to the first normalized index at/after it.
    norm_start = 0
    if start > 0:
        while norm_start < len(view.norm_to_raw) and view.norm_to_raw[norm_start] < start:
            norm_start += 1

    spans: list[tuple[int, int]] = []
    cursor = norm_start
    while True:
        idx = view.normalized.find(needle_norm, cursor)
        if idx < 0:
            break
        end = idx + len(needle_norm)
        raw_start, raw_end = view.raw_span(idx, end)
        spans.append((raw_start, raw_end))
        cursor = end
    return spans


def find_next_whitespace_normalized(
    raw: str,
    needle: str,
    *,
    view: WhitespaceNormalizedView | None = None,
    norm_start: int = 0,
) -> tuple[int, int, int] | None:
    """
    Find the next match starting at ``norm_start`` in the normalized view.

    Returns ``(raw_start, raw_end, next_norm_start)`` or ``None``.
    ``next_norm_start`` is the normalized index to continue from after a
    rejected identity validation (advance by one normalized character).
    """
    needle_norm = normalize_needle(needle)
    if not needle_norm:
        return None
    resolved = view or build_whitespace_normalized_view(raw)
    if not resolved.normalized:
        return None
    idx = resolved.normalized.find(needle_norm, norm_start)
    if idx < 0:
        return None
    end = idx + len(needle_norm)
    raw_start, raw_end = resolved.raw_span(idx, end)
    return raw_start, raw_end, idx + 1
