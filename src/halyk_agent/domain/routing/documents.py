"""Document ↔ scenario routing with exact-ID-first precedence."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from halyk_agent.domain.ids import deterministic_id
from halyk_agent.domain.parsing import CanonicalDocument
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
from halyk_agent.domain.routing.normalize import names_match_exact


@dataclass(frozen=True, slots=True)
class DocumentRoutingBundle:
    links: tuple[DocumentEntityLink, ...]
    conflicts: tuple[EntityResolutionConflict, ...]
    diagnostics: tuple[RoutingDiagnostic, ...]
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


def route_documents(
    documents: tuple[CanonicalDocument, ...],
    *,
    account_extractions: tuple[AccountIdentity, ...],
    borrowers: tuple[BorrowerIdentity, ...],
    scenario_accounts: dict[str, frozenset[str]],
    borrower_name_by_scenario: dict[str, frozenset[str]],
) -> DocumentRoutingBundle:
    """
    Precedence:
      1. EXPLICIT_ACCOUNT_ID (EXACT)
      2. EXPLICIT_BORROWER_DECLARATION (DECLARED)
      3. DECLARED_ENTITY_RELATION (DECLARED) — recorded only when relation text exists
      4. NORMALIZED_LEGAL_NAME (DERIVED) — full token equality only
      5. UNRESOLVED
    Account identifiers always outrank name similarity.
    """
    accounts_by_doc: dict[str, list[AccountIdentity]] = defaultdict(list)
    for account in account_extractions:
        if account.document_id:
            accounts_by_doc[account.document_id].append(account)

    borrowers_by_doc: dict[str, list[BorrowerIdentity]] = defaultdict(list)
    for borrower in borrowers:
        borrowers_by_doc[borrower.document_id].append(borrower)

    account_scenarios = _account_to_scenarios(scenario_accounts)
    # Flatten borrower normalized names for LEVEL 4 matching.
    name_to_scenarios: dict[str, set[str]] = defaultdict(set)
    for scenario_id, names in borrower_name_by_scenario.items():
        for name in names:
            name_to_scenarios[name].add(scenario_id)

    links: list[DocumentEntityLink] = []
    conflicts: list[EntityResolutionConflict] = []
    diagnostics: list[RoutingDiagnostic] = []
    resolved = 0
    unresolved = 0
    multi = 0

    for document in sorted(documents, key=lambda d: d.document_id):
        doc_accounts = accounts_by_doc.get(document.document_id, [])
        doc_borrowers = borrowers_by_doc.get(document.document_id, [])

        # LEVEL 1 — exact account IDs mapped to scenarios
        mapped_accounts = [
            acc for acc in doc_accounts if acc.account_id_normalized in account_scenarios
        ]
        scenario_hits: dict[str, list[AccountIdentity]] = defaultdict(list)
        for acc in mapped_accounts:
            for scenario_id in account_scenarios[acc.account_id_normalized]:
                scenario_hits[scenario_id].append(acc)

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

            # IDENTIFIER_NAME_CONFLICT: name points elsewhere but account wins.
            name_conflict_scenarios: set[str] = set()
            for borrower in doc_borrowers:
                for other_scenario, names in borrower_name_by_scenario.items():
                    if other_scenario == scenario_id:
                        continue
                    if borrower.normalized_name in names or any(
                        names_match_exact(borrower.legal_name_raw, name) for name in names
                    ):
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
                            "normalized company name resembles another scenario; "
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

        # LEVEL 2 — explicit borrower declaration anchored to a known account/scenario
        declared_scenarios: set[str] = set()
        declared_accounts: set[str] = set()
        declared_spans: set[str] = set()
        for borrower in doc_borrowers:
            declared_spans.add(borrower.evidence_span_id)
            if (
                borrower.account_id_normalized
                and borrower.account_id_normalized in account_scenarios
            ):
                declared_accounts.add(borrower.account_id_normalized)
                declared_scenarios.update(account_scenarios[borrower.account_id_normalized])
            elif borrower.normalized_name in name_to_scenarios:
                declared_scenarios.update(name_to_scenarios[borrower.normalized_name])

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

        # LEVEL 4 — exact normalized legal-name equality against anchored borrowers
        # (LEVEL 3 DECLARED_ENTITY_RELATION is reserved for explicit relation records;
        # this stage records relation candidates via borrower patterns only.)
        name_hits: set[str] = set()
        name_spans: set[str] = set()
        for borrower in doc_borrowers:
            if borrower.normalized_name in name_to_scenarios:
                name_hits.update(name_to_scenarios[borrower.normalized_name])
                name_spans.add(borrower.evidence_span_id)

        if len(name_hits) == 1:
            scenario_id = next(iter(name_hits))
            links.append(
                DocumentEntityLink(
                    document_id=document.document_id,
                    document_version_id=document.document_version_id,
                    scenario_ids=(scenario_id,),
                    account_ids=(),
                    method=ResolutionMethod.NORMALIZED_LEGAL_NAME,
                    confidence=ResolutionConfidence.DERIVED,
                    evidence_span_ids=tuple(sorted(name_spans)),
                )
            )
            resolved += 1
            continue
        if len(name_hits) > 1:
            multi += 1
            links.append(
                DocumentEntityLink(
                    document_id=document.document_id,
                    document_version_id=document.document_version_id,
                    scenario_ids=tuple(sorted(name_hits)),
                    account_ids=(),
                    method=ResolutionMethod.NORMALIZED_LEGAL_NAME,
                    confidence=ResolutionConfidence.DERIVED,
                    evidence_span_ids=tuple(sorted(name_spans)),
                    notes="MULTI_SCENARIO_DOCUMENT",
                )
            )
            conflicts.append(
                EntityResolutionConflict(
                    conflict_id=deterministic_id(
                        "multi-scenario-name-doc-v1",
                        document.document_id,
                        *sorted(name_hits),
                    ),
                    kind=ConflictKind.MULTI_SCENARIO_DOCUMENT,
                    severity=DiagnosticSeverity.WARNING,
                    scenario_ids=tuple(sorted(name_hits)),
                    document_ids=(document.document_id,),
                    detail="normalized names match multiple scenarios",
                )
            )
            resolved += 1
            continue

        # LEVEL 5 — unresolved / noise
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
    return DocumentRoutingBundle(
        links=tuple(links),
        conflicts=tuple(conflicts),
        diagnostics=tuple(diagnostics),
        resolved_count=resolved,
        unresolved_count=unresolved,
        multi_scenario_count=multi,
    )
