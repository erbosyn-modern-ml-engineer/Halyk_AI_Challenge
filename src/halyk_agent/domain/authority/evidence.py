"""Evidence span helpers for Stage 5C taxonomy/authority."""

from __future__ import annotations

from halyk_agent.domain.evidence import EvidenceSpan
from halyk_agent.domain.parsing import CanonicalDocument
from halyk_agent.domain.routing.evidence import IdentitySpanResult, create_identity_span


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
        end = idx + len(pattern)
        # Align end to raw length of matched region (same char count for ASCII;
        # for Cyrillic casefold length equals original when lengths match).
        # Prefer slicing by the original pattern length from the casefold index
        # when character lengths are equal (true for Russian/Kazakh casefold).
        raw_slice = text[idx : idx + len(pattern)]
        if casefold and raw_slice.casefold() != needle:
            # Fallback: expand until casefold equals needle.
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
