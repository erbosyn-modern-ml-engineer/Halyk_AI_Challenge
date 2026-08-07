"""Document ↔ scenario routing with exact-ID-first precedence (Stage 5B.1)."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from halyk_agent.domain.evidence import EvidenceSpan
from halyk_agent.domain.ids import deterministic_id
from halyk_agent.domain.parsing import CanonicalDocument
from halyk_agent.domain.routing.evidence import create_identity_span
from halyk_agent.domain.routing.models import (
    AccountIdentity,
    BorrowerIdentity,
    ConflictKind,
    DiagnosticCode,
    DiagnosticSeverity,
    DocumentEntityLink,
    EntityResolutionConflict,
    ResolutionConfidence,
    ResolutionMethod,
    RoutingDiagnostic,
)
from halyk_agent.domain.routing.normalize import (
    legal_form_mismatch,
    normalize_legal_name_keys,
)

_RELATION_MARKER_RE = re.compile(
    r"(?i)\b(?:"
    r"segment|subsidiary|group|standalone\s+subsidiary|"
    r"conducted\s+through|operated\s+through|"
    r"\u0441\u0435\u0433\u043c\u0435\u043d\u0442|"
    r"\u0434\u043e\u0447\u0435\u0440\u043d\w*|"
    r"\u0433\u0440\u0443\u043f\u043f\u0430|"
    r"\u043e\u0441\u0443\u0449\u0435\u0441\u0442\u0432\u043b\u044f\u0435\u0442\u0441\u044f\s+\u0447\u0435\u0440\u0435\u0437|"
    r"\u043f\u0440\u0435\u0434\u0441\u0442\u0430\u0432\u043b\u0435\u043d\s+\u0447\u0435\u0440\u0435\u0437|"
    r"\u0435\u043d\u0448\u0456\u043b\u0435\u0441|"
    r"\u0442\u043e\u043f|"
    r"\u0430\u0440\u049b\u044b\u043b\u044b"
    r")\b"
)

# Prefer longer raw mentions first to avoid partial collisions.
_WINDOW = 320


@dataclass(frozen=True, slots=True)
class DocumentRoutingBundle:
    links: tuple[DocumentEntityLink, ...]
    conflicts: tuple[EntityResolutionConflict, ...]
    diagnostics: tuple[RoutingDiagnostic, ...]
    spans: tuple[EvidenceSpan, ...]
    resolved_count: int
    unresolved_count: int
    multi_scenario_count: int


def _account_to_scenarios(
    scenario_accounts: dict[str, frozenset[str]],
) -> dict[str, frozenset[str]]:
    inverse: dict[str, set[str]] = defaultdict(set)
    for scenario_id, accounts in scenario_accounts.items():
        for account_id in accounts:
            inverse[account_id].add(scenario_id)
    return {account: frozenset(scenarios) for account, scenarios in inverse.items()}


def _find_identity_mentions(
    document: CanonicalDocument,
    *,
    search_terms: tuple[tuple[str, str, str], ...],
    # (raw_needle, identity_key, scenario_id)
) -> list[tuple[str, str, EvidenceSpan, bool]]:
    """Return (scenario_id, identity_key, span, has_relation_marker)."""
    hits: list[tuple[str, str, EvidenceSpan, bool]] = []
    seen_span_ids: set[str] = set()
    for page in document.pages:
        text = page.raw_text or ""
        for raw_needle, identity_key, scenario_id in search_terms:
            start = 0
            while True:
                idx = text.find(raw_needle, start)
                if idx < 0:
                    break
                end = idx + len(raw_needle)
                # Validate identity_key equality on the matched substring.
                matched = text[idx:end]
                keys = normalize_legal_name_keys(matched, record_aliases=False)
                if keys.identity_key != identity_key:
                    start = idx + 1
                    continue
                span_result = create_identity_span(
                    document,
                    page_number=page.page_number,
                    char_start=idx,
                    char_end=end,
                )
                start = end
                if span_result.rejected_low_trust_ocr or span_result.span is None:
                    continue
                span = span_result.span
                if span.id in seen_span_ids:
                    continue
                seen_span_ids.add(span.id)
                window = text[max(0, idx - _WINDOW) : min(len(text), end + _WINDOW)]
                has_relation = _RELATION_MARKER_RE.search(window) is not None
                hits.append((scenario_id, identity_key, span, has_relation))
    return hits


def route_documents(
    documents: tuple[CanonicalDocument, ...],
    *,
    account_extractions: tuple[AccountIdentity, ...],
    borrowers: tuple[BorrowerIdentity, ...],
    scenario_accounts: dict[str, frozenset[str]],
    borrower_identity_by_scenario: dict[str, frozenset[str]],
    borrower_raw_by_identity: dict[str, tuple[str, ...]],
) -> DocumentRoutingBundle:
    """
    Precedence:
      1. EXPLICIT_ACCOUNT_ID (EXACT)
      2. EXPLICIT_BORROWER_DECLARATION (DECLARED) — declaration + account anchor
      3. GROUP_SEGMENT_DECLARATION (DECLARED)
      4. NORMALIZED_LEGAL_NAME (DERIVED) — identity_key equality only
      5. UNRESOLVED
    """
    accounts_by_doc: dict[str, list[AccountIdentity]] = defaultdict(list)
    for account in account_extractions:
        if account.document_id:
            accounts_by_doc[account.document_id].append(account)

    borrowers_by_doc: dict[str, list[BorrowerIdentity]] = defaultdict(list)
    for borrower in borrowers:
        borrowers_by_doc[borrower.document_id].append(borrower)

    account_scenarios = _account_to_scenarios(scenario_accounts)

    # Search terms from anchored borrower identity keys only.
    search_terms: list[tuple[str, str, str]] = []
    for scenario_id, identity_keys in borrower_identity_by_scenario.items():
        for identity_key in identity_keys:
            for raw in borrower_raw_by_identity.get(identity_key, ()):
                search_terms.append((raw, identity_key, scenario_id))
    search_terms.sort(key=lambda item: (-len(item[0]), item[0], item[1], item[2]))

    links: list[DocumentEntityLink] = []
    conflicts: list[EntityResolutionConflict] = []
    diagnostics: list[RoutingDiagnostic] = []
    extra_spans: list[EvidenceSpan] = []
    resolved = 0
    unresolved = 0
    multi = 0

    for document in sorted(documents, key=lambda d: d.document_id):
        doc_accounts = accounts_by_doc.get(document.document_id, [])
        doc_borrowers = borrowers_by_doc.get(document.document_id, [])

        mapped_accounts = [
            acc for acc in doc_accounts if acc.account_id_normalized in account_scenarios
        ]
        scenario_hits: dict[str, list[AccountIdentity]] = defaultdict(list)
        for acc in mapped_accounts:
            for scenario_id in account_scenarios[acc.account_id_normalized]:
                scenario_hits[scenario_id].append(acc)

        # LEVEL 1 — exact account
        if len(scenario_hits) > 1:
            multi += 1
            span_ids = tuple(
                sorted(
                    {
                        acc.evidence_span_id
                        for accs in scenario_hits.values()
                        for acc in accs
                        if acc.evidence_span_id
                    }
                )
            )
            account_ids = tuple(sorted({acc.account_id_normalized for acc in mapped_accounts}))
            scenario_ids = tuple(sorted(scenario_hits))
            conflicts.append(
                EntityResolutionConflict(
                    conflict_id=deterministic_id(
                        "multi-scenario-doc-v1",
                        document.document_id,
                        *scenario_ids,
                    ),
                    kind=ConflictKind.MULTI_SCENARIO_DOCUMENT,
                    severity=DiagnosticSeverity.WARNING,
                    scenario_ids=scenario_ids,
                    account_ids=account_ids,
                    document_ids=(document.document_id,),
                    evidence_span_ids=span_ids,
                    detail="document contains account IDs belonging to multiple scenarios",
                )
            )
            diagnostics.append(
                RoutingDiagnostic(
                    code=DiagnosticCode.DOCUMENT_MULTIPLE_SCENARIOS,
                    severity=DiagnosticSeverity.WARNING,
                    message=(
                        f"document {document.document_id} maps to multiple scenarios: "
                        f"{', '.join(scenario_ids)}"
                    ),
                    document_id=document.document_id,
                )
            )
            links.append(
                DocumentEntityLink(
                    document_id=document.document_id,
                    document_version_id=document.document_version_id,
                    scenario_ids=scenario_ids,
                    account_ids=account_ids,
                    method=ResolutionMethod.EXPLICIT_ACCOUNT_ID,
                    confidence=ResolutionConfidence.EXACT,
                    evidence_span_ids=span_ids,
                    notes="MULTI_SCENARIO_DOCUMENT",
                )
            )
            resolved += 1
            continue

        if len(scenario_hits) == 1:
            scenario_id = next(iter(scenario_hits))
            accs = scenario_hits[scenario_id]
            account_ids = tuple(sorted({a.account_id_normalized for a in accs}))
            span_ids = tuple(sorted({a.evidence_span_id for a in accs if a.evidence_span_id}))

            # Name conflict diagnostics (account retains precedence).
            name_conflict_scenarios: set[str] = set()
            for borrower in doc_borrowers:
                for other_scenario, keys in borrower_identity_by_scenario.items():
                    if other_scenario == scenario_id:
                        continue
                    if borrower.identity_key in keys:
                        name_conflict_scenarios.add(other_scenario)
            if name_conflict_scenarios:
                conflicts.append(
                    EntityResolutionConflict(
                        conflict_id=deterministic_id(
                            "id-name-conflict-v1",
                            document.document_id,
                            scenario_id,
                            *sorted(name_conflict_scenarios),
                        ),
                        kind=ConflictKind.IDENTIFIER_NAME_CONFLICT,
                        severity=DiagnosticSeverity.WARNING,
                        scenario_ids=(scenario_id, *sorted(name_conflict_scenarios)),
                        account_ids=account_ids,
                        document_ids=(document.document_id,),
                        evidence_span_ids=span_ids,
                        detail=(
                            "identity_key resembles another scenario; "
                            "account identifier retains precedence"
                        ),
                    )
                )
                diagnostics.append(
                    RoutingDiagnostic(
                        code=DiagnosticCode.DOCUMENT_IDENTIFIER_NAME_CONFLICT,
                        severity=DiagnosticSeverity.WARNING,
                        message=(
                            f"document {document.document_id}: account routes to "
                            f"{scenario_id}; name resembles "
                            f"{', '.join(sorted(name_conflict_scenarios))}"
                        ),
                        document_id=document.document_id,
                        scenario_id=scenario_id,
                    )
                )

            links.append(
                DocumentEntityLink(
                    document_id=document.document_id,
                    document_version_id=document.document_version_id,
                    scenario_ids=(scenario_id,),
                    account_ids=account_ids,
                    method=ResolutionMethod.EXPLICIT_ACCOUNT_ID,
                    confidence=ResolutionConfidence.EXACT,
                    evidence_span_ids=span_ids,
                )
            )
            resolved += 1
            continue

        # LEVEL 2 — explicit borrower declaration anchored to known account
        declared_scenarios: set[str] = set()
        declared_accounts: set[str] = set()
        declared_spans: set[str] = set()
        for borrower in doc_borrowers:
            if not borrower.account_id_normalized:
                continue
            if borrower.account_id_normalized not in account_scenarios:
                continue
            declared_spans.add(borrower.evidence_span_id)
            declared_accounts.add(borrower.account_id_normalized)
            declared_scenarios.update(account_scenarios[borrower.account_id_normalized])

        if len(declared_scenarios) == 1:
            scenario_id = next(iter(declared_scenarios))
            links.append(
                DocumentEntityLink(
                    document_id=document.document_id,
                    document_version_id=document.document_version_id,
                    scenario_ids=(scenario_id,),
                    account_ids=tuple(sorted(declared_accounts)),
                    method=ResolutionMethod.EXPLICIT_BORROWER_DECLARATION,
                    confidence=ResolutionConfidence.DECLARED,
                    evidence_span_ids=tuple(sorted(declared_spans)),
                )
            )
            resolved += 1
            continue
        if len(declared_scenarios) > 1:
            multi += 1
            conflicts.append(
                EntityResolutionConflict(
                    conflict_id=deterministic_id(
                        "multi-scenario-borrower-doc-v1",
                        document.document_id,
                        *sorted(declared_scenarios),
                    ),
                    kind=ConflictKind.MULTI_SCENARIO_DOCUMENT,
                    severity=DiagnosticSeverity.WARNING,
                    scenario_ids=tuple(sorted(declared_scenarios)),
                    document_ids=(document.document_id,),
                    evidence_span_ids=tuple(sorted(declared_spans)),
                    detail="borrower declarations point to multiple scenarios",
                )
            )
            links.append(
                DocumentEntityLink(
                    document_id=document.document_id,
                    document_version_id=document.document_version_id,
                    scenario_ids=tuple(sorted(declared_scenarios)),
                    account_ids=tuple(sorted(declared_accounts)),
                    method=ResolutionMethod.EXPLICIT_BORROWER_DECLARATION,
                    confidence=ResolutionConfidence.DECLARED,
                    evidence_span_ids=tuple(sorted(declared_spans)),
                    notes="MULTI_SCENARIO_DOCUMENT",
                )
            )
            resolved += 1
            continue

        # LEVEL 3 / 4 — exact identity mentions in evidence-bearing text
        mentions = _find_identity_mentions(document, search_terms=tuple(search_terms))
        if mentions:
            for _, _, span, _ in mentions:
                extra_spans.append(span)

            relation_scenarios = {s for s, _, _, rel in mentions if rel}
            name_scenarios = {s for s, _, _, _ in mentions}
            # Legal-form mismatch diagnostics vs other known forms with same base.
            for borrower in doc_borrowers:
                for scenario_id, keys in borrower_identity_by_scenario.items():
                    for key in keys:
                        # Reconstruct a comparison raw from known raws
                        for raw in borrower_raw_by_identity.get(key, ()):
                            if legal_form_mismatch(borrower.legal_name_raw, raw):
                                diagnostics.append(
                                    RoutingDiagnostic(
                                        code=DiagnosticCode.LEGAL_FORM_MISMATCH,
                                        severity=DiagnosticSeverity.INFO,
                                        message=(
                                            f"legal-form mismatch for document "
                                            f"{document.document_id}: "
                                            f"{borrower.legal_name_raw!r} vs {raw!r}"
                                        ),
                                        document_id=document.document_id,
                                        scenario_id=scenario_id,
                                    )
                                )
                                conflicts.append(
                                    EntityResolutionConflict(
                                        conflict_id=deterministic_id(
                                            "legal-form-mismatch-v1",
                                            document.document_id,
                                            borrower.identity_key,
                                            key,
                                        ),
                                        kind=ConflictKind.LEGAL_FORM_MISMATCH,
                                        severity=DiagnosticSeverity.INFO,
                                        scenario_ids=(scenario_id,),
                                        document_ids=(document.document_id,),
                                        detail=(
                                            "base_key matches but legal form differs; not linked"
                                        ),
                                    )
                                )

            if relation_scenarios:
                if len(relation_scenarios) > 1:
                    multi += 1
                    span_ids = tuple(sorted({span.id for _, _, span, rel in mentions if rel}))
                    conflicts.append(
                        EntityResolutionConflict(
                            conflict_id=deterministic_id(
                                "multi-scenario-group-doc-v1",
                                document.document_id,
                                *sorted(relation_scenarios),
                            ),
                            kind=ConflictKind.MULTI_SCENARIO_DOCUMENT,
                            severity=DiagnosticSeverity.WARNING,
                            scenario_ids=tuple(sorted(relation_scenarios)),
                            document_ids=(document.document_id,),
                            evidence_span_ids=span_ids,
                            detail="group/segment markers for multiple scenarios",
                        )
                    )
                    links.append(
                        DocumentEntityLink(
                            document_id=document.document_id,
                            document_version_id=document.document_version_id,
                            scenario_ids=tuple(sorted(relation_scenarios)),
                            account_ids=(),
                            method=ResolutionMethod.GROUP_SEGMENT_DECLARATION,
                            confidence=ResolutionConfidence.DECLARED,
                            evidence_span_ids=span_ids,
                            notes="MULTI_SCENARIO_DOCUMENT",
                            group_document=True,
                            relation_type="GROUP_SEGMENT",
                        )
                    )
                    diagnostics.append(
                        RoutingDiagnostic(
                            code=DiagnosticCode.GROUP_DOCUMENT,
                            severity=DiagnosticSeverity.INFO,
                            message=f"group document {document.document_id} is multi-scenario",
                            document_id=document.document_id,
                        )
                    )
                    resolved += 1
                    continue

                scenario_id = next(iter(relation_scenarios))
                span_ids = tuple(
                    sorted({span.id for s, _, span, rel in mentions if rel and s == scenario_id})
                )
                links.append(
                    DocumentEntityLink(
                        document_id=document.document_id,
                        document_version_id=document.document_version_id,
                        scenario_ids=(scenario_id,),
                        account_ids=(),
                        method=ResolutionMethod.GROUP_SEGMENT_DECLARATION,
                        confidence=ResolutionConfidence.DECLARED,
                        evidence_span_ids=span_ids,
                        group_document=True,
                        relation_type="GROUP_SEGMENT",
                    )
                )
                diagnostics.append(
                    RoutingDiagnostic(
                        code=DiagnosticCode.GROUP_DOCUMENT,
                        severity=DiagnosticSeverity.INFO,
                        message=(
                            f"group/segment declaration routes document "
                            f"{document.document_id} to {scenario_id}"
                        ),
                        document_id=document.document_id,
                        scenario_id=scenario_id,
                    )
                )
                resolved += 1
                continue

            # LEVEL 4 — exact identity_key without relation marker
            if len(name_scenarios) == 1:
                scenario_id = next(iter(name_scenarios))
                span_ids = tuple(
                    sorted({span.id for s, _, span, _ in mentions if s == scenario_id})
                )
                links.append(
                    DocumentEntityLink(
                        document_id=document.document_id,
                        document_version_id=document.document_version_id,
                        scenario_ids=(scenario_id,),
                        account_ids=(),
                        method=ResolutionMethod.NORMALIZED_LEGAL_NAME,
                        confidence=ResolutionConfidence.DERIVED,
                        evidence_span_ids=span_ids,
                    )
                )
                resolved += 1
                continue
            if len(name_scenarios) > 1:
                multi += 1
                span_ids = tuple(sorted({span.id for _, _, span, _ in mentions}))
                conflicts.append(
                    EntityResolutionConflict(
                        conflict_id=deterministic_id(
                            "multi-scenario-name-doc-v1",
                            document.document_id,
                            *sorted(name_scenarios),
                        ),
                        kind=ConflictKind.MULTI_SCENARIO_DOCUMENT,
                        severity=DiagnosticSeverity.WARNING,
                        scenario_ids=tuple(sorted(name_scenarios)),
                        document_ids=(document.document_id,),
                        evidence_span_ids=span_ids,
                        detail="exact identity keys match multiple scenarios",
                    )
                )
                links.append(
                    DocumentEntityLink(
                        document_id=document.document_id,
                        document_version_id=document.document_version_id,
                        scenario_ids=tuple(sorted(name_scenarios)),
                        account_ids=(),
                        method=ResolutionMethod.NORMALIZED_LEGAL_NAME,
                        confidence=ResolutionConfidence.DERIVED,
                        evidence_span_ids=span_ids,
                        notes="MULTI_SCENARIO_DOCUMENT",
                    )
                )
                resolved += 1
                continue

        # LEVEL 5 — unresolved
        unresolved += 1
        diagnostics.append(
            RoutingDiagnostic(
                code=DiagnosticCode.DOCUMENT_NO_ENTITY_SIGNAL,
                severity=DiagnosticSeverity.INFO,
                message=f"document {document.document_id} has no scenario entity signal",
                document_id=document.document_id,
            )
        )
        links.append(
            DocumentEntityLink(
                document_id=document.document_id,
                document_version_id=document.document_version_id,
                scenario_ids=(),
                account_ids=tuple(sorted({a.account_id_normalized for a in doc_accounts})),
                method=ResolutionMethod.UNRESOLVED,
                confidence=ResolutionConfidence.UNRESOLVED,
                evidence_span_ids=tuple(
                    sorted({a.evidence_span_id for a in doc_accounts if a.evidence_span_id})
                ),
                notes="NO_ENTITY_SIGNAL",
            )
        )

    links.sort(key=lambda item: item.document_id)
    extra_spans.sort(key=lambda item: item.id)
    return DocumentRoutingBundle(
        links=tuple(links),
        conflicts=tuple(conflicts),
        diagnostics=tuple(diagnostics),
        spans=tuple(extra_spans),
        resolved_count=resolved,
        unresolved_count=unresolved,
        multi_scenario_count=multi,
    )
