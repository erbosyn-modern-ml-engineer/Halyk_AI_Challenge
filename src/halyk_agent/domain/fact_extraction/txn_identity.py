"""Trusted-ledger transaction identity: exact vocabulary, strict token locate.

Documents never invent transaction identities from a ``TXN-*`` shape guess.
Only IDs present in the trusted ledger vocabulary may be attached to facts.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Iterator

# Opaque ledger IDs may contain hyphens; treat hyphen as an in-token character so
# ``TXN-KC-CAP-29`` does not match inside ``TXN-KC-CAP-29-FAKE``.
_TOKEN_CHAR = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")


def build_txn_id_vocabulary(txn_ids: Iterable[str]) -> frozenset[str]:
    """Build the exact trusted transaction-ID vocabulary."""
    return frozenset(value.strip() for value in txn_ids if value and value.strip())


def is_complete_token_span(text: str, start: int, end: int) -> bool:
    """True when ``text[start:end]`` is a complete token under txn-id boundaries."""
    if start < 0 or end > len(text) or start >= end:
        return False
    if start > 0 and text[start - 1] in _TOKEN_CHAR:
        return False
    return not (end < len(text) and text[end] in _TOKEN_CHAR)


def iter_txn_id_spans(
    text: str,
    vocabulary: Collection[str] | None,
) -> Iterator[tuple[int, int, str]]:
    """Yield ``(start, end, txn_id)`` for exact complete-token vocabulary hits.

    Longer vocabulary IDs win at overlapping positions. Order is document order.
    """
    if not text or not vocabulary:
        return iter(())
    ordered = sorted({item for item in vocabulary if item}, key=len, reverse=True)
    claimed: list[tuple[int, int]] = []
    hits: list[tuple[int, int, str]] = []
    for txn_id in ordered:
        cursor = 0
        while True:
            idx = text.find(txn_id, cursor)
            if idx < 0:
                break
            end = idx + len(txn_id)
            cursor = idx + 1
            if not is_complete_token_span(text, idx, end):
                continue
            if any(not (end <= left or idx >= right) for left, right in claimed):
                continue
            claimed.append((idx, end))
            hits.append((idx, end, txn_id))
    hits.sort(key=lambda item: item[0])
    return iter(hits)


def find_txn_ids(text: str, vocabulary: Collection[str] | None = None) -> tuple[str, ...]:
    """Return trusted ledger txn IDs found in ``text`` (deduped, document order).

    When ``vocabulary`` is missing/empty, returns ``()`` — a document string that
    merely looks like ``TXN-*`` never creates a new identity.
    """
    seen: set[str] = set()
    out: list[str] = []
    for _start, _end, txn_id in iter_txn_id_spans(text, vocabulary):
        if txn_id in seen:
            continue
        seen.add(txn_id)
        out.append(txn_id)
    return tuple(out)


def count_txn_id_mentions(text: str, vocabulary: Collection[str] | None) -> int:
    """Count complete-token vocabulary occurrences (including repeats)."""
    return sum(1 for _ in iter_txn_id_spans(text, vocabulary))


def corpus_has_ledger_txn_id(text: str, vocabulary: Collection[str] | None) -> bool:
    """True when any trusted ledger txn ID appears as a complete token."""
    return next(iter_txn_id_spans(text, vocabulary), None) is not None


def txn_near_pattern(
    text: str,
    vocabulary: Collection[str] | None,
    *,
    tail_pattern: re.Pattern[str],
    max_gap: int = 200,
) -> bool:
    """True when a vocabulary txn ID is followed (within ``max_gap``) by ``tail_pattern``."""
    if not vocabulary:
        return False
    for _start, end, _txn_id in iter_txn_id_spans(text, vocabulary):
        window = text[end : end + max_gap]
        newline = window.find("\n")
        if newline >= 0:
            window = window[:newline]
        if tail_pattern.search(window):
            return True
    return False


def txn_id_capture_pattern(vocabulary: Collection[str] | None) -> str | None:
    """Regex fragment with named group ``txn`` for embedding in extractors.

    Uses longest-first alternation and the same token boundaries as locate.
    """
    if not vocabulary:
        return None
    ordered = sorted({item for item in vocabulary if item}, key=len, reverse=True)
    if not ordered:
        return None
    alternation = "|".join(re.escape(item) for item in ordered)
    return rf"(?<![A-Za-z0-9_-])(?P<txn>{alternation})(?![A-Za-z0-9_-])"
