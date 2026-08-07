"""Document ↔ scenario routing with exact-ID-first precedence (Stage 5B.2)."""

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
from halyk_agent.domain.routing.whitespace_search import (
    build_whitespace_normalized_view,
    find_next_whitespace_normalized,
    normalize_needle,
)

# Strong structural relation predicates only.
# Bare nouns (group / группа / топ / segment / сегмент) are intentionally absent.
_STRONG_RELATION_RE = re.compile(
    r"(?ix)"
    r"(?:"
    r"wholly[\s_-]+owned[\s_-]+subsidiar(?:y|ies)|"
    r"majority[\s_-]*owned[\s_-]+subsidiar(?:y|ies)|"
    r"standalone[\s_-]+subsidiar(?:y|ies)|"
    r"subsidiaries|"
    r"subsidiary|"
    r"parent[\s_-]+compan(?:y|ies)|"
    r"segment\s+is\s+conducted\s+through|"
    r"segment\s+is\s+operated\s+through|"
    r"conducted\s+through|"
    r"operated\s+through|"
    r"held\s+through|"
    r"\u0434\u043e\u0447\u0435\u0440\u043d\w*|"
    r"\u043e\u0441\u0443\u0449\u0435\u0441\u0442\u0432\u043b\u044f\u0435\u0442\u0441\u044f\s+\u0447\u0435\u0440\u0435\u0437|"
    r"\u0434\u0435\u044f\u0442\u0435\u043b\u044c\u043d\u043e\u0441\u0442\u044c\s+\u0432\u0435\u0434\u0435\u0442\u0441\u044f\s+\u0447\u0435\u0440\u0435\u0437|"
    r"\u0432\u0445\u043e\u0434\u0438\u0442\s+\u0432\s+\u0433\u0440\u0443\u043f\u043f\u0443|"
    r"\u043c\u0430\u0442\u0435\u0440\u0438\u043d\u0441\u043a\u0430\u044f\s+\u043a\u043e\u043c\u043f\u0430\u043d\u0438\u044f|"
    r"\u043a\u043e\u043d\u0441\u043e\u043b\u0438\u0434\u0438\u0440\u043e\u0432\u0430\u043d\u043d\u0430\u044f\s+\u0433\u0440\u0443\u043f\u043f\u0430|"
    r"\u0435\u043d\u0448\u0456\u043b\u0435\u0441\s+(?:\u043a\u043e\u043c\u043f\u0430\u043d\u0438\u044f|\u04b1\u0439\u044b\u043c)|"
    r"\u0431\u0430\u0441\s+\u043a\u043e\u043c\u043f\u0430\u043d\u0438\u044f|"
    r"\u0442\u043e\u043f\s+\u049b\u04b1\u0440\u0430\u043c\u044b\u043d\u0430\s+\u043a\u0456\u0440\u0435\u0434\u0456|"
    r".{0,40}\u0430\u0440\u049b\u044b\u043b\u044b\s+\u0436\u04af\u0437\u0435\u0433\u0435\s+\u0430\u0441\u044b\u0440\u044b\u043b\u0430\u0434\u044b"
    r")"
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


@dataclass(frozen=True, slots=True)
class _IdentityMention:
    scenario_id: str
    identity_key: str
    entity_span: EvidenceSpan
    page_number: int
    char_start: int
    char_end: int
    relation_span: EvidenceSpan | None
    relation_type: str | None


def _account_to_scenarios(
    scenario_accounts: dict[str, frozenset[str]],
) -> dict[str, frozenset[str]]:
    inverse: dict[str, set[str]] = defaultdict(set)
    for scenario_id, accounts in scenario_accounts.items():
        for account_id in accounts:
            inverse[account_id].add(scenario_id)
    return {account: frozenset(scenarios) for account, scenarios in inverse.items()}


def _classify_relation(matched: str) -> str:
    low = matched.casefold()
    if (
        "subsidiar" in low
        or "\u0434\u043e\u0447\u0435\u0440\u043d" in low
        or "\u0435\u043d\u0448\u0456\u043b\u0435\u0441" in low
    ):
        return "SUBSIDIARY"
    if (
        "through" in low
        or "\u0447\u0435\u0440\u0435\u0437" in low
        or "\u0430\u0440\u049b\u044b\u043b\u044b" in low
    ):
        return "OPERATED_THROUGH"
    if (
        "parent" in low
        or "\u043c\u0430\u0442\u0435\u0440\u0438\u043d\u0441\u043a" in low
        or "\u0431\u0430\u0441 \u043a\u043e\u043c\u043f\u0430\u043d" in low
    ):
        return "PARENT"
    if (
        "\u0432\u0445\u043e\u0434\u0438\u0442 \u0432 \u0433\u0440\u0443\u043f\u043f" in low
        or "\u0442\u043e\u043f \u049b\u04b1\u0440\u0430\u043c\u044b\u043d\u0430" in low
        or "\u043a\u043e\u043d\u0441\u043e\u043b\u0438\u0434\u0438\u0440\u043e\u0432" in low
    ):
        return "GROUP_MEMBERSHIP"
    return "STRUCTURAL_RELATION"


def _relation_in_window(
    document: CanonicalDocument,
    *,
    page_number: int,
    text: str,
    entity_start: int,
    entity_end: int,
) -> tuple[EvidenceSpan | None, str | None]:
    window_start = max(0, entity_start - _WINDOW)
    window_end = min(len(text), entity_end + _WINDOW)
    window = text[window_start:window_end]
    match = _STRONG_RELATION_RE.search(window)
    if match is None:
        return None, None
    rel_start = window_start + match.start()
    rel_end = window_start + match.end()
    span_result = create_identity_span(
        document,
        page_number=page_number,
        char_start=rel_start,
        char_end=rel_end,
    )
    if span_result.rejected_low_trust_ocr or span_result.span is None:
        # Relation predicate itself may be OCR; still allow entity-only LEVEL 4.
        return None, None
    return span_result.span, _classify_relation(match.group(0))


def _find_identity_mentions(
    document: CanonicalDocument,
    *,
    search_terms: tuple[tuple[str, str, str], ...],
    # (raw_needle, identity_key, scenario_id)
) -> list[_IdentityMention]:
    """Return identity mentions with optional bounded structural relation spans."""
    hits: list[_IdentityMention] = []
    seen_span_ids: set[str] = set()
    for page in document.pages:
        text = page.raw_text or ""
        if not text:
            continue
        view = build_whitespace_normalized_view(text)
        for raw_needle, identity_key, scenario_id in search_terms:
            if not normalize_needle(raw_needle):
                continue
            norm_cursor = 0
            while True:
                found = find_next_whitespace_normalized(
                    text,
                    raw_needle,
                    view=view,
                    norm_start=norm_cursor,
                )
                if found is None:
                    break
                idx, end, reject_advance = found
                matched = text[idx:end]
                keys = normalize_legal_name_keys(matched, record_aliases=False)
                if keys.identity_key != identity_key:
                    norm_cursor = reject_advance
                    continue
                span_result = create_identity_span(
                    document,
                    page_number=page.page_number,
                    char_start=idx,
                    char_end=end,
                )
                # Advance past this accepted match in normalized space.
                needle_norm_len = len(normalize_needle(raw_needle))
                # Map raw end back: continue after this match.
                # reject_advance is idx+1 in norm; for success advance by full needle.
                # Recompute accepted norm start from raw idx via view.
                accepted_norm_start = 0
                while (
                    accepted_norm_start < len(view.norm_to_raw)
                    and view.norm_to_raw[accepted_norm_start] < idx
                ):
                    accepted_norm_start += 1
                norm_cursor = accepted_norm_start + needle_norm_len

                if span_result.rejected_low_trust_ocr or span_result.span is None:
                    continue
                span = span_result.span
                if span.id in seen_span_ids:
                    continue
                seen_span_ids.add(span.id)
                relation_span, relation_type = _relation_in_window(
                    document,
                    page_number=page.page_number,
                    text=text,
                    entity_start=idx,
                    entity_end=end,
                )
                if relation_span is not None and relation_span.id not in seen_span_ids:
                    seen_span_ids.add(relation_span.id)
                hits.append(
                    _IdentityMention(
                        scenario_id=scenario_id,
                        identity_key=identity_key,
                        entity_span=span,
                        page_number=page.page_number,
                        char_start=idx,
                        char_end=end,
                        relation_span=relation_span,
                        relation_type=relation_type,
                    )
                )
    return hits


def _scenarios_in_local_window(
    mentions: list[_IdentityMention],
    *,
    page_number: int,
    center_start: int,
    center_end: int,
    page_len: int,
) -> frozenset[str]:
    window_start = max(0, center_start - _WINDOW)
    window_end = min(page_len, center_end + _WINDOW)
    found: set[str] = set()
    for mention in mentions:
        if mention.page_number != page_number:
            continue
        if mention.char_end <= window_start or mention.char_start >= window_end:
            continue
        found.add(mention.scenario_id)
    return frozenset(found)


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
      3. GROUP_SEGMENT_DECLARATION (DECLARED) — anchored identity + structural relation
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
                        f"document {document.document_id} maps to multiple scenarios "
                        f"via account IDs: {', '.join(scenario_ids)}"
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
            accounts = scenario_hits[scenario_id]
            account_ids = tuple(sorted({a.account_id_normalized for a in accounts}))
            span_ids = tuple(
                sorted({acc.evidence_span_id for acc in accounts if acc.evidence_span_id})
            )
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

        # LEVEL 2 — anchored borrower declaration on this document
        declared_scenarios: set[str] = set()
        declared_accounts: set[str] = set()
        declared_spans: set[str] = set()
        for borrower in doc_borrowers:
            if not borrower.anchored or not borrower.account_id_normalized:
                continue
            if borrower.account_id_normalized not in account_scenarios:
                continue
            declared_accounts.add(borrower.account_id_normalized)
            declared_spans.add(borrower.evidence_span_id)
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
            for mention in mentions:
                extra_spans.append(mention.entity_span)
                if mention.relation_span is not None:
                    extra_spans.append(mention.relation_span)

            name_scenarios = {m.scenario_id for m in mentions}
            # Legal-form mismatch diagnostics vs other known forms with same base.
            for borrower in doc_borrowers:
                for scenario_id, keys in borrower_identity_by_scenario.items():
                    for key in keys:
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

            # LEVEL 3 — structural relation + exact anchored identity in local window
            page_lengths = {page.page_number: len(page.raw_text or "") for page in document.pages}
            relation_scenarios: set[str] = set()
            relation_ambiguous = False
            relation_span_ids: set[str] = set()
            relation_types: set[str] = set()
            for mention in mentions:
                if mention.relation_span is None or mention.relation_type is None:
                    continue
                local = _scenarios_in_local_window(
                    mentions,
                    page_number=mention.page_number,
                    center_start=min(
                        mention.char_start,
                        mention.relation_span.char_start
                        if mention.relation_span.char_start is not None
                        else mention.char_start,
                    ),
                    center_end=max(
                        mention.char_end,
                        mention.relation_span.char_end
                        if mention.relation_span.char_end is not None
                        else mention.char_end,
                    ),
                    page_len=page_lengths.get(mention.page_number, 0),
                )
                if len(local) > 1:
                    relation_ambiguous = True
                    relation_scenarios.update(local)
                else:
                    relation_scenarios.add(mention.scenario_id)
                relation_span_ids.add(mention.entity_span.id)
                relation_span_ids.add(mention.relation_span.id)
                relation_types.add(mention.relation_type)

            if relation_scenarios:
                relation_type = (
                    sorted(relation_types)[0] if len(relation_types) == 1 else "GROUP_SEGMENT"
                )
                span_ids = tuple(sorted(relation_span_ids))
                if relation_ambiguous or len(relation_scenarios) > 1:
                    multi += 1
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
                            detail=(
                                "structural relation context contains exact identities "
                                "from multiple scenarios"
                            ),
                        )
                    )
                    diagnostics.append(
                        RoutingDiagnostic(
                            code=DiagnosticCode.DOCUMENT_MULTIPLE_SCENARIOS,
                            severity=DiagnosticSeverity.WARNING,
                            message=(
                                f"group relation context in {document.document_id} "
                                f"names multiple scenarios"
                            ),
                            document_id=document.document_id,
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
                            relation_type=relation_type,
                        )
                    )
                    resolved += 1
                    continue

                scenario_id = next(iter(relation_scenarios))
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
                        relation_type=relation_type,
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

            # LEVEL 4 — exact identity_key without structural relation
            if len(name_scenarios) == 1:
                scenario_id = next(iter(name_scenarios))
                span_ids = tuple(
                    sorted({m.entity_span.id for m in mentions if m.scenario_id == scenario_id})
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
                span_ids = tuple(sorted({m.entity_span.id for m in mentions}))
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
