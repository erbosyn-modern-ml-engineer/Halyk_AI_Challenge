"""Exact account-identifier extraction from canonical documents."""

from __future__ import annotations

import re
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

# Complete ACC-family tokens only. ACC-7801 and ACC-7801-08 are distinct.
ACCOUNT_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9-])"
    r"(ACC-[0-9]+(?:-[0-9]+)*)"
    r"(?![A-Za-z0-9-])"
)

# Malformed near-misses for diagnostics (not accepted as identities).
_MALFORMED_ACCOUNT_RE = re.compile(
    r"(?<![A-Za-z0-9-])(ACC-(?:[A-Za-z][A-Za-z0-9-]*|[0-9]*[A-Za-z]+[0-9A-Za-z-]*))(?![A-Za-z0-9-])"
)


@dataclass(frozen=True, slots=True)
class AccountExtractionBundle:
    accounts: tuple[AccountIdentity, ...]
    spans: tuple[EvidenceSpan, ...]
    diagnostics: tuple[RoutingDiagnostic, ...]
    conflicts: tuple[EntityResolutionConflict, ...]


def extract_account_identities(
    document: CanonicalDocument,
) -> AccountExtractionBundle:
    """Extract exact account tokens with EvidenceSpan provenance."""
    accounts: list[AccountIdentity] = []
    spans: list[EvidenceSpan] = []
    diagnostics: list[RoutingDiagnostic] = []
    conflicts: list[EntityResolutionConflict] = []
    seen: set[tuple[str, int, str]] = set()

    for page in document.pages:
        text = page.raw_text or ""
        for match in ACCOUNT_TOKEN_RE.finditer(text):
            raw = match.group(1)
            normalized = normalize_account_id(raw)
            key = (normalized, page.page_number, raw)
            if key in seen:
                continue
            seen.add(key)
            span_result = create_identity_span(
                document,
                page_number=page.page_number,
                char_start=match.start(1),
                char_end=match.end(1),
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
