"""Exact account-identifier extraction from canonical documents."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from halyk_agent.domain.evidence import EvidenceSpan
from halyk_agent.domain.ids import deterministic_id
from halyk_agent.domain.parsing import CanonicalDocument
from halyk_agent.domain.routing.evidence import create_identity_span
from halyk_agent.domain.routing.models import (
    AccountIdentity,
    ConflictKind,
    DiagnosticCode,
    DiagnosticSeverity,
    EntityResolutionConflict,
    RoutingDiagnostic,
)
from halyk_agent.domain.routing.normalize import normalize_account_id

# Token boundary shared by every account-identifier recognizer. An account token
# is complete: ACC-7801 is never recovered from inside ACC-7801-08.
_LEFT_BOUNDARY = r"(?<![A-Za-z0-9-])"
_RIGHT_BOUNDARY = r"(?![A-Za-z0-9-])"

# Complete ACC-family tokens only. ACC-7801 and ACC-7801-08 are distinct.
# This is a *supplementary* recognizer for the historically observed ACC-family,
# never the definition of what an account identifier is — see AccountVocabulary.
ACCOUNT_TOKEN_RE = re.compile(_LEFT_BOUNDARY + r"(ACC-[0-9]+(?:-[0-9]+)*)" + _RIGHT_BOUNDARY)

# Malformed near-misses for diagnostics (not accepted as identities).
_MALFORMED_ACCOUNT_RE = re.compile(
    r"(?<![A-Za-z0-9-])(ACC-(?:[A-Za-z][A-Za-z0-9-]*|[0-9]*[A-Za-z]+[0-9A-Za-z-]*))(?![A-Za-z0-9-])"
)


@dataclass(frozen=True, slots=True)
class AccountVocabulary:
    """Exact account identifiers observed in trusted dataset evidence.

    Account identifiers are *opaque*: the dataset — not a formatting convention —
    decides which strings are account identifiers. The vocabulary is derived from
    ledger rows, so an identifier like ``TELE-4471`` participates in routing on
    exactly the same footing as ``ACC-7001``, with no per-prefix special-casing.
    Recognition stays exact: complete-token matching, no fuzzy or substring hits.
    """

    identifiers: frozenset[str]
    pattern: re.Pattern[str] | None


EMPTY_ACCOUNT_VOCABULARY = AccountVocabulary(identifiers=frozenset(), pattern=None)


def build_account_vocabulary(raw_account_ids: Iterable[str]) -> AccountVocabulary:
    """Compile an exact-match recognizer for observed account identifiers."""
    normalized = {normalize_account_id(raw) for raw in raw_account_ids if raw and raw.strip()}
    normalized.discard("")
    if not normalized:
        return EMPTY_ACCOUNT_VOCABULARY
    # Longest first so an identifier that extends another wins the alternation.
    alternation = "|".join(
        re.escape(token) for token in sorted(normalized, key=lambda item: (-len(item), item))
    )
    pattern = re.compile(
        _LEFT_BOUNDARY + "(" + alternation + ")" + _RIGHT_BOUNDARY,
        re.IGNORECASE,
    )
    return AccountVocabulary(identifiers=frozenset(normalized), pattern=pattern)


def find_account_tokens(
    text: str,
    *,
    vocabulary: AccountVocabulary | None = None,
) -> list[tuple[int, int, str]]:
    """Return ``(start, end, raw_token)`` for every complete account token.

    Union of the observed-identifier vocabulary and the supplementary ACC-family
    recognizer, deduplicated by character span and ordered by position.
    """
    found: dict[tuple[int, int], str] = {}
    if vocabulary is not None and vocabulary.pattern is not None:
        for match in vocabulary.pattern.finditer(text):
            found[(match.start(1), match.end(1))] = match.group(1)
    for match in ACCOUNT_TOKEN_RE.finditer(text):
        found.setdefault((match.start(1), match.end(1)), match.group(1))
    return [(start, end, raw) for (start, end), raw in sorted(found.items())]


@dataclass(frozen=True, slots=True)
class AccountExtractionBundle:
    accounts: tuple[AccountIdentity, ...]
    spans: tuple[EvidenceSpan, ...]
    diagnostics: tuple[RoutingDiagnostic, ...]
    conflicts: tuple[EntityResolutionConflict, ...]


def extract_account_identities(
    document: CanonicalDocument,
    *,
    vocabulary: AccountVocabulary | None = None,
) -> AccountExtractionBundle:
    """Extract exact account tokens with EvidenceSpan provenance."""
    accounts: list[AccountIdentity] = []
    spans: list[EvidenceSpan] = []
    diagnostics: list[RoutingDiagnostic] = []
    conflicts: list[EntityResolutionConflict] = []
    seen: set[tuple[str, int, str]] = set()

    for page in document.pages:
        text = page.raw_text or ""
        for char_start, char_end, raw in find_account_tokens(text, vocabulary=vocabulary):
            normalized = normalize_account_id(raw)
            key = (normalized, page.page_number, raw)
            if key in seen:
                continue
            seen.add(key)
            span_result = create_identity_span(
                document,
                page_number=page.page_number,
                char_start=char_start,
                char_end=char_end,
            )
            if span_result.rejected_low_trust_ocr:
                diagnostics.append(
                    RoutingDiagnostic(
                        code=DiagnosticCode.OCR_IDENTITY_LOW_TRUST,
                        severity=DiagnosticSeverity.WARNING,
                        message=(
                            f"OCR-derived account {normalized} rejected: "
                            "missing OCR backend provenance"
                        ),
                        document_id=document.document_id,
                        account_id=normalized,
                    )
                )
                continue
            span = span_result.span
            if span is None:
                continue
            spans.append(span)
            accounts.append(
                AccountIdentity(
                    account_id_raw=raw,
                    account_id_normalized=normalized,
                    document_id=document.document_id,
                    document_version_id=document.document_version_id,
                    page_number=page.page_number,
                    evidence_span_id=span.id,
                    text_origin=span.text_origin.value,
                    ocr_backend_identity=span.ocr_backend_identity,
                    ocr_configuration_hash=span.ocr_configuration_hash,
                )
            )

        for match in _MALFORMED_ACCOUNT_RE.finditer(text):
            raw = match.group(1)
            if ACCOUNT_TOKEN_RE.fullmatch(raw) or ACCOUNT_TOKEN_RE.search(raw):
                # Prefer the well-formed token already captured.
                continue
            if vocabulary is not None and normalize_account_id(raw) in vocabulary.identifiers:
                # Observed in the ledger — the dataset defines the identifier, not
                # the ACC-family shape.
                continue
            diagnostics.append(
                RoutingDiagnostic(
                    code=DiagnosticCode.ACCOUNT_ID_MALFORMED,
                    severity=DiagnosticSeverity.INFO,
                    message=f"malformed account-like token ignored: {raw}",
                    document_id=document.document_id,
                    account_id=raw,
                )
            )
            conflicts.append(
                EntityResolutionConflict(
                    conflict_id=deterministic_id(
                        "account-malformed-v1",
                        document.document_id,
                        page.page_number,
                        raw,
                    ),
                    kind=ConflictKind.ACCOUNT_ID_MALFORMED,
                    severity=DiagnosticSeverity.INFO,
                    document_ids=(document.document_id,),
                    account_ids=(raw,),
                    detail=f"malformed account-like token: {raw}",
                )
            )

    accounts.sort(
        key=lambda item: (
            item.account_id_normalized,
            item.page_number or 0,
            item.evidence_span_id or "",
        )
    )
    # Diagnose prefix collisions among extracted complete tokens (ACC-7801 vs ACC-7801-08).
    norms = sorted({a.account_id_normalized for a in accounts})
    for i, shorter in enumerate(norms):
        for longer in norms[i + 1 :]:
            if is_prefix_collision(shorter, longer):
                diagnostics.append(
                    RoutingDiagnostic(
                        code=DiagnosticCode.ACCOUNT_ID_MALFORMED,
                        severity=DiagnosticSeverity.INFO,
                        message=(
                            f"complete-token prefix collision observed: "
                            f"{shorter} vs {longer} (kept distinct)"
                        ),
                        document_id=document.document_id,
                        account_id=shorter,
                    )
                )
    spans.sort(key=lambda item: item.id)
    return AccountExtractionBundle(
        accounts=tuple(accounts),
        spans=tuple(spans),
        diagnostics=tuple(diagnostics),
        conflicts=tuple(conflicts),
    )


def is_prefix_collision(shorter: str, longer: str) -> bool:
    """True when longer extends shorter with an extra '-' segment (not equality)."""
    if shorter == longer:
        return False
    return longer.startswith(shorter + "-")
