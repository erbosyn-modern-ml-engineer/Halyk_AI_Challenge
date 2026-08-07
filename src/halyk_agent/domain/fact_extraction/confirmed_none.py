"""Deterministic CONFIRMED_NONE detectors (explicit negatives; not rejected facts)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from halyk_agent.domain.fact_extraction.models import FactKind
from halyk_agent.domain.fact_extraction.text_locate import page_text_slices
from halyk_agent.domain.parsing import CanonicalDocument

_RECLASS_NONE_PATTERNS = (
    re.compile(
        r"Переклассификац\w*\s+за\s+ковенантн\w*\s+период\w*\s+не\s+требовал\w*",
        re.IGNORECASE,
    ),
    re.compile(
        r"переклассификац\w*\s+не\s+требовал\w*",
        re.IGNORECASE,
    ),
    re.compile(
        r"No\s+reclassification(?:s)?\s+(?:was|were)\s+required",
        re.IGNORECASE,
    ),
    re.compile(
        r"reclassification(?:s)?\s+(?:was|were)\s+not\s+(?:required|needed)",
        re.IGNORECASE,
    ),
    re.compile(
        r"no\s+reclassifications?\s+(?:were|was)\s+(?:required|needed)",
        re.IGNORECASE,
    ),
)


@dataclass(frozen=True, slots=True)
class ConfirmedNoneHit:
    quote: str
    page_number: int
    char_start: int
    char_end: int
    reason_code: str


def detect_confirmed_none(
    fact_kind: FactKind,
    document: CanonicalDocument,
) -> ConfirmedNoneHit | None:
    """Return an explicit-negative hit when the source confirms absence of facts."""
    if fact_kind is FactKind.TRANSACTION_RECLASSIFICATION:
        for page_number, text in page_text_slices(document):
            for pattern in _RECLASS_NONE_PATTERNS:
                match = pattern.search(text)
                if match is None:
                    continue
                return ConfirmedNoneHit(
                    quote=text[match.start() : match.end()],
                    page_number=page_number,
                    char_start=match.start(),
                    char_end=match.end(),
                    reason_code="CONFIRMED_NONE_RECLASS",
                )
    return None
