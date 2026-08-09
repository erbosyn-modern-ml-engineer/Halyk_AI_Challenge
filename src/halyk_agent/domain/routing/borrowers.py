"""Explicit borrower declaration extraction (identity only)."""

from __future__ import annotations

# Multilingual borrower/legal-form literals are intentional.
# ruff: noqa: RUF001
import re
from dataclasses import dataclass

from halyk_agent.domain.evidence import EvidenceSpan
from halyk_agent.domain.parsing import CanonicalDocument
from halyk_agent.domain.routing.accounts import AccountVocabulary, find_account_tokens
from halyk_agent.domain.routing.evidence import create_identity_span
from halyk_agent.domain.routing.models import BorrowerIdentity
from halyk_agent.domain.routing.normalize import (
    normalize_account_id,
    normalize_legal_name_keys,
)

_COMPANY_NAME = (
    r"(?P<name>(?-i:"
    r"(?:[A-Z][A-Za-z0-9&.'\-]*(?:\s+[A-Z][A-Za-z0-9&.'\-]*){0,8}\s+(?:JSC|LLP|LLC|Inc\.?|Ltd\.?))"
    r"|(?:[\u0410-\u042f\u0401][\u0410-\u044f\u0401\u04510-9&.'\-]*"
    r"(?:\s+[\u0410-\u042f\u0401][\u0410-\u044f\u0401\u04510-9&.'\-]*){0,8}"
    r"\s+(?:\u0410\u041e|\u0422\u041e\u041e))"
    r"))"
)

_BORROWER_WORD = (
    r"(?:Borrower|\u0417\u0430\u0451\u043c\u0449\u0438\u043a|"
    r"\u0417\u0430\u0435\u043c\u0449\u0438\u043a|"
    r"\u049a\u0430\u0440\u044b\u0437 \u0430\u043b\u0443\u0448\u044b)"
)

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "DASH_BORROWER_PAREN",
        re.compile(
            _COMPANY_NAME
            + r"\s*\([^)]{0,160}\)\s*\(\s*"
            + "\u0434\u0430\u043b\u0435\u0435"
            + r"\s*[—\-–]\s*[«\"]?\s*"
            + _BORROWER_WORD
            + r"[»\"]?\s*\)",
            re.IGNORECASE,
        ),
    ),
    (
        "BORROWER_COMMA_NAME",
        re.compile(
            _BORROWER_WORD + r"\s*[,:：]\s*" + _COMPANY_NAME,
            re.IGNORECASE,
        ),
    ),
    (
        "TO_BORROWER_PAREN",
        re.compile(
            r"(?:к\s+\u0417\u0430\u0451\u043c\u0449\u0438\u043a\u0443|"
            r"к\s+\u0417\u0430\u0435\u043c\u0449\u0438\u043a\u0443|"
            r"to\s+the\s+Borrower)\s*\(\s*" + _COMPANY_NAME + r"\s*\)",
            re.IGNORECASE,
        ),
    ),
    (
        "EXPLICIT_LABEL",
        re.compile(
            _BORROWER_WORD + r"\s*[:：]\s*" + _COMPANY_NAME,
            re.IGNORECASE,
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class BorrowerExtractionBundle:
    borrowers: tuple[BorrowerIdentity, ...]
    spans: tuple[EvidenceSpan, ...]


def _nearby_account(
    text: str,
    start: int,
    end: int,
    *,
    vocabulary: AccountVocabulary | None = None,
) -> str | None:
    window_start = max(0, start - 240)
    window_end = min(len(text), end + 240)
    window = text[window_start:window_end]
    matches = find_account_tokens(window, vocabulary=vocabulary)
    if not matches:
        return None
    primary = [item for item in matches if item[2].count("-") == 1]
    chosen = primary[0] if primary else matches[0]
    return normalize_account_id(chosen[2])


def extract_borrower_declarations(
    document: CanonicalDocument,
    *,
    vocabulary: AccountVocabulary | None = None,
) -> BorrowerExtractionBundle:
    """Extract explicit borrower declarations with evidence (no authority selection)."""
    borrowers: list[BorrowerIdentity] = []
    spans: list[EvidenceSpan] = []
    seen: set[tuple[str, str, int]] = set()

    for page in document.pages:
        text = page.raw_text or ""
        for declaration_type, pattern in _PATTERNS:
            for match in pattern.finditer(text):
                raw_name = re.sub(r"\s+", " ", match.group("name")).strip()
                if not raw_name:
                    continue
                normalized_keys = normalize_legal_name_keys(raw_name)
                if not normalized_keys.identity_key:
                    continue
                key = (normalized_keys.identity_key, declaration_type, page.page_number)
                if key in seen:
                    continue
                name_start = match.start("name")
                name_end = match.end("name")
                span_result = create_identity_span(
                    document,
                    page_number=page.page_number,
                    char_start=name_start,
                    char_end=name_end,
                )
                if span_result.rejected_low_trust_ocr or span_result.span is None:
                    continue
                span = span_result.span
                seen.add(key)
                spans.append(span)
                borrowers.append(
                    BorrowerIdentity(
                        legal_name_raw=raw_name,
                        normalized_name=normalized_keys.identity_key,
                        identity_key=normalized_keys.identity_key,
                        base_key=normalized_keys.base_key,
                        declaration_type=declaration_type,
                        document_id=document.document_id,
                        document_version_id=document.document_version_id,
                        page_number=page.page_number,
                        evidence_span_id=span.id,
                        account_id_normalized=_nearby_account(
                            text,
                            match.start(),
                            match.end(),
                            vocabulary=vocabulary,
                        ),
                        scenario_id=None,
                        anchored=False,
                    )
                )

    borrowers.sort(
        key=lambda item: (
            item.normalized_name,
            item.document_id,
            item.page_number,
            item.declaration_type,
        )
    )
    spans.sort(key=lambda item: item.id)
    return BorrowerExtractionBundle(borrowers=tuple(borrowers), spans=tuple(spans))
