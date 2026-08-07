"""Deterministic authority resolution (Stage 5C)."""

from __future__ import annotations

from collections import defaultdict

from halyk_agent.domain.authority.models import (
    AuthorityConflict,
    AuthorityDecision,
    AuthorityDomain,
    AuthorityStatus,
    DocumentClassification,
    DocumentFamily,
    DocumentLifecycleStatus,
    DocumentMetadata,
    DocumentType,
)
from halyk_agent.domain.ids import deterministic_id

_REQUIRED_DOMAINS = (
    AuthorityDomain.COVENANT_TERMS,
    AuthorityDomain.FINANCIAL_ADJUSTMENTS,
    AuthorityDomain.KYC_RELATIONSHIPS,
)

_OPTIONAL_DOMAINS = (
    AuthorityDomain.GROUP_STRUCTURE,
    AuthorityDomain.TREASURY_FACTS,
)

_ACTIVE_LIFECYCLES = frozenset(
    {
        DocumentLifecycleStatus.CURRENT_EXECUTED,
        DocumentLifecycleStatus.FINAL,
        DocumentLifecycleStatus.APPROVED,
        DocumentLifecycleStatus.EFFECTIVE,
        DocumentLifecycleStatus.CURRENT,
    }
)

_REJECT_LIFECYCLES = frozenset(
    {
        DocumentLifecycleStatus.SUPERSEDED,
        DocumentLifecycleStatus.OBSOLETE,
        DocumentLifecycleStatus.EXPIRED,
        DocumentLifecycleStatus.DRAFT,
        DocumentLifecycleStatus.PRELIMINARY,
        DocumentLifecycleStatus.WORKING_PAPER,
    }
)

_DOMAIN_TYPES: dict[AuthorityDomain, frozenset[DocumentType]] = {
    AuthorityDomain.COVENANT_TERMS: frozenset({DocumentType.LOAN_AGREEMENT}),
    AuthorityDomain.FINANCIAL_ADJUSTMENTS: frozenset(
        {
            DocumentType.AUDITOR_REPORT,
            DocumentType.AGREED_UPON_PROCEDURES_REPORT,
        }
    ),
    AuthorityDomain.KYC_RELATIONSHIPS: frozenset({DocumentType.KYC_DOSSIER}),
    AuthorityDomain.GROUP_STRUCTURE: frozenset({DocumentType.GROUP_OR_CONSOLIDATED_REPORT}),
    AuthorityDomain.TREASURY_FACTS: frozenset({DocumentType.TREASURY_MEMO}),
}


def _rank_lifecycle(status: DocumentLifecycleStatus) -> int:
    order = {
        DocumentLifecycleStatus.CURRENT_EXECUTED: 100,
        DocumentLifecycleStatus.FINAL: 90,
        DocumentLifecycleStatus.APPROVED: 85,
        DocumentLifecycleStatus.EFFECTIVE: 80,
        DocumentLifecycleStatus.CURRENT: 75,
        DocumentLifecycleStatus.UNKNOWN: 20,
        DocumentLifecycleStatus.DRAFT: 10,
        DocumentLifecycleStatus.PRELIMINARY: 8,
        DocumentLifecycleStatus.WORKING_PAPER: 5,
        DocumentLifecycleStatus.SUPERSEDED: 0,
        DocumentLifecycleStatus.OBSOLETE: 0,
        DocumentLifecycleStatus.EXPIRED: 0,
    }
    return order.get(status, 0)


def _date_key(metadata: DocumentMetadata | None) -> str:
    if metadata is None:
        return ""
    return (
        metadata.effective_date
        or metadata.execution_date
        or metadata.document_date
        or metadata.report_date
        or ""
    )


def resolve_authority(
    *,
    classifications: tuple[DocumentClassification, ...],
    metadata_by_id: dict[str, DocumentMetadata],
    families: tuple[DocumentFamily, ...],
    scenario_ids: tuple[str, ...],
) -> tuple[tuple[AuthorityDecision, ...], tuple[AuthorityConflict, ...]]:
    """
    Resolve authoritative documents per scenario and domain.

    Prefer explicit lifecycle signals over dates. Never silently pick among
    equal authoritative candidates — emit AuthorityConflict instead.
    False authoritative is worse than MISSING_AUTHORITY.
    """
    by_scenario: dict[str, list[DocumentClassification]] = defaultdict(list)
    for item in classifications:
        for scenario_id in item.scenario_ids:
            by_scenario[scenario_id].append(item)

    family_by_doc: dict[str, DocumentFamily] = {}
    for family in families:
        for doc_id in family.document_ids:
            family_by_doc[doc_id] = family

    decisions: list[AuthorityDecision] = []
    conflicts: list[AuthorityConflict] = []

    for scenario_id in sorted(scenario_ids):
        docs = by_scenario.get(scenario_id, [])
        for domain in (*_REQUIRED_DOMAINS, *_OPTIONAL_DOMAINS):
            allowed_types = _DOMAIN_TYPES[domain]
            candidates = [d for d in docs if d.document_type in allowed_types]
            if not candidates:
                if domain in _REQUIRED_DOMAINS:
                    decisions.append(
                        AuthorityDecision(
                            decision_id=deterministic_id(
                                "authority-decision-v1",
                                scenario_id,
                                domain.value,
                                "missing",
                            ),
                            scenario_id=scenario_id,
                            domain=domain,
                            status=AuthorityStatus.MISSING_AUTHORITY,
                            rule_id="RULE_MISSING_AUTHORITY",
                            reason=f"no typed candidate documents for {domain.value}",
                            candidate_document_ids=(),
                        )
                    )
                continue

            active = [
                d
                for d in candidates
                if d.lifecycle_status in _ACTIVE_LIFECYCLES
                or (
                    d.lifecycle_status is DocumentLifecycleStatus.UNKNOWN
                    and d.document_type is DocumentType.AUDITOR_REPORT
                )
            ]
            rejected = [d for d in candidates if d.lifecycle_status in _REJECT_LIFECYCLES]

            # Prefer same-family active docs when family exists.
            if domain is AuthorityDomain.COVENANT_TERMS and active:
                # Partition by family; pick best family with active executed.
                by_family: dict[str, list[DocumentClassification]] = defaultdict(list)
                for item in active:
                    fam = family_by_doc.get(item.document_id)
                    fam_id = fam.family_id if fam else f"solo:{item.document_id}"
                    by_family[fam_id].append(item)
                # Prefer CURRENT_EXECUTED over UNKNOWN.
                scored: list[tuple[int, str, list[DocumentClassification]]] = []
                for fam_id, items in by_family.items():
                    best_rank = max(_rank_lifecycle(i.lifecycle_status) for i in items)
                    scored.append((best_rank, fam_id, items))
                scored.sort(key=lambda row: (-row[0], row[1]))
                best_rank = scored[0][0]
                if best_rank <= 0:
                    active = []
                else:
                    top_families = [row for row in scored if row[0] == best_rank]
                    # Multiple distinct current/executed agreement families → conflict.
                    if len(top_families) > 1:
                        winners = [item for _, _, items in top_families for item in items]
                        conflict = AuthorityConflict(
                            conflict_id=deterministic_id(
                                "authority-conflict-v1",
                                scenario_id,
                                domain.value,
                                *sorted(w.document_id for w in winners),
                            ),
                            scenario_id=scenario_id,
                            domain=domain,
                            candidate_document_ids=tuple(sorted(w.document_id for w in winners)),
                            evidence_span_ids=tuple(
                                sorted({sid for w in winners for sid in w.evidence_span_ids})
                            ),
                            reason=(
                                "multiple current executed loan agreements "
                                "with distinct agreement identities"
                            ),
                        )
                        conflicts.append(conflict)
                        decisions.append(
                            AuthorityDecision(
                                decision_id=deterministic_id(
                                    "authority-decision-v1",
                                    scenario_id,
                                    domain.value,
                                    "unresolved",
                                ),
                                scenario_id=scenario_id,
                                domain=domain,
                                status=AuthorityStatus.UNRESOLVED,
                                rule_id="RULE_EQUAL_AUTHORITATIVE_CONFLICT",
                                reason=conflict.reason,
                                winning_document_ids=(),
                                rejected_document_ids=tuple(
                                    sorted(r.document_id for r in rejected)
                                ),
                                candidate_document_ids=tuple(
                                    sorted(c.document_id for c in candidates)
                                ),
                                evidence_span_ids=conflict.evidence_span_ids,
                            )
                        )
                        continue
                    best_fam, best_items = top_families[0][1], top_families[0][2]
                    winners = [
                        i for i in best_items if _rank_lifecycle(i.lifecycle_status) == best_rank
                    ]
                    if len(winners) > 1:
                        conflict = AuthorityConflict(
                            conflict_id=deterministic_id(
                                "authority-conflict-v1",
                                scenario_id,
                                domain.value,
                                *sorted(w.document_id for w in winners),
                            ),
                            scenario_id=scenario_id,
                            domain=domain,
                            candidate_document_ids=tuple(sorted(w.document_id for w in winners)),
                            evidence_span_ids=tuple(
                                sorted({sid for w in winners for sid in w.evidence_span_ids})
                            ),
                            reason="multiple equal authoritative loan agreements",
                        )
                        conflicts.append(conflict)
                        decisions.append(
                            AuthorityDecision(
                                decision_id=deterministic_id(
                                    "authority-decision-v1",
                                    scenario_id,
                                    domain.value,
                                    "unresolved",
                                ),
                                scenario_id=scenario_id,
                                domain=domain,
                                status=AuthorityStatus.UNRESOLVED,
                                rule_id="RULE_EQUAL_AUTHORITATIVE_CONFLICT",
                                reason=conflict.reason,
                                winning_document_ids=(),
                                rejected_document_ids=tuple(
                                    sorted(r.document_id for r in rejected)
                                ),
                                candidate_document_ids=tuple(
                                    sorted(c.document_id for c in candidates)
                                ),
                                evidence_span_ids=conflict.evidence_span_ids,
                                family_id=best_fam if not best_fam.startswith("solo:") else None,
                            )
                        )
                        continue
                    winner = sorted(winners, key=lambda w: w.document_id)[0]
                    decisions.append(
                        AuthorityDecision(
                            decision_id=deterministic_id(
                                "authority-decision-v1",
                                scenario_id,
                                domain.value,
                                winner.document_id,
                            ),
                            scenario_id=scenario_id,
                            domain=domain,
                            status=AuthorityStatus.AUTHORITATIVE,
                            rule_id="RULE_AGREEMENT_EXPLICIT_SUPERSEDED_CURRENT",
                            reason=(
                                "current/executed agreement outranks superseded/obsolete "
                                "or weaker lifecycle candidates"
                            ),
                            winning_document_ids=(winner.document_id,),
                            rejected_document_ids=tuple(
                                sorted(
                                    {
                                        *(r.document_id for r in rejected),
                                        *(
                                            a.document_id
                                            for a in active
                                            if a.document_id != winner.document_id
                                        ),
                                    }
                                )
                            ),
                            candidate_document_ids=tuple(sorted(c.document_id for c in candidates)),
                            evidence_span_ids=winner.evidence_span_ids,
                            family_id=family_by_doc[winner.document_id].family_id
                            if winner.document_id in family_by_doc
                            else None,
                        )
                    )
                    continue

            if domain is AuthorityDomain.FINANCIAL_ADJUSTMENTS:
                finals = [
                    d
                    for d in candidates
                    if d.lifecycle_status
                    in {
                        DocumentLifecycleStatus.FINAL,
                        DocumentLifecycleStatus.APPROVED,
                    }
                ]
                drafts = [
                    d
                    for d in candidates
                    if d.lifecycle_status
                    in {
                        DocumentLifecycleStatus.DRAFT,
                        DocumentLifecycleStatus.PRELIMINARY,
                        DocumentLifecycleStatus.WORKING_PAPER,
                    }
                ]
                if not finals:
                    decisions.append(
                        AuthorityDecision(
                            decision_id=deterministic_id(
                                "authority-decision-v1",
                                scenario_id,
                                domain.value,
                                "missing",
                            ),
                            scenario_id=scenario_id,
                            domain=domain,
                            status=AuthorityStatus.MISSING_AUTHORITY,
                            rule_id="RULE_MISSING_FINAL_AUDIT",
                            reason="no FINAL/APPROVED auditor or AUP document",
                            rejected_document_ids=tuple(sorted(d.document_id for d in drafts)),
                            candidate_document_ids=tuple(sorted(c.document_id for c in candidates)),
                        )
                    )
                    continue
                # Prefer AUDITOR_REPORT FINAL over AUP when both exist.
                auditor_finals = [
                    d for d in finals if d.document_type is DocumentType.AUDITOR_REPORT
                ]
                pool = auditor_finals or finals
                if len(pool) > 1:
                    # Tie-break by report/document date only when same type and no
                    # explicit supersession among them — else conflict.
                    dated = sorted(
                        pool,
                        key=lambda d: (
                            _date_key(metadata_by_id.get(d.document_id)),
                            d.document_id,
                        ),
                        reverse=True,
                    )
                    top_date = _date_key(metadata_by_id.get(dated[0].document_id))
                    tied = [
                        d for d in dated if _date_key(metadata_by_id.get(d.document_id)) == top_date
                    ]
                    if len(tied) > 1 or not top_date:
                        conflict = AuthorityConflict(
                            conflict_id=deterministic_id(
                                "authority-conflict-v1",
                                scenario_id,
                                domain.value,
                                *sorted(d.document_id for d in pool),
                            ),
                            scenario_id=scenario_id,
                            domain=domain,
                            candidate_document_ids=tuple(sorted(d.document_id for d in pool)),
                            reason=(
                                "multiple FINAL auditor/AUP documents without supersession chain"
                            ),
                        )
                        conflicts.append(conflict)
                        decisions.append(
                            AuthorityDecision(
                                decision_id=deterministic_id(
                                    "authority-decision-v1",
                                    scenario_id,
                                    domain.value,
                                    "unresolved",
                                ),
                                scenario_id=scenario_id,
                                domain=domain,
                                status=AuthorityStatus.UNRESOLVED,
                                rule_id="RULE_EQUAL_FINAL_AUDIT_CONFLICT",
                                reason=conflict.reason,
                                rejected_document_ids=tuple(sorted(d.document_id for d in drafts)),
                                candidate_document_ids=tuple(
                                    sorted(c.document_id for c in candidates)
                                ),
                            )
                        )
                        continue
                    winner = dated[0]
                else:
                    winner = pool[0]
                decisions.append(
                    AuthorityDecision(
                        decision_id=deterministic_id(
                            "authority-decision-v1",
                            scenario_id,
                            domain.value,
                            winner.document_id,
                        ),
                        scenario_id=scenario_id,
                        domain=domain,
                        status=AuthorityStatus.AUTHORITATIVE,
                        rule_id="RULE_FINAL_AUDIT_OUTRANKS_DRAFT",
                        reason="FINAL/APPROVED auditor output outranks draft/preliminary",
                        winning_document_ids=(winner.document_id,),
                        rejected_document_ids=tuple(
                            sorted(
                                {
                                    *(d.document_id for d in drafts),
                                    *(
                                        d.document_id
                                        for d in finals
                                        if d.document_id != winner.document_id
                                    ),
                                }
                            )
                        ),
                        candidate_document_ids=tuple(sorted(c.document_id for c in candidates)),
                        evidence_span_ids=winner.evidence_span_ids,
                    )
                )
                continue

            # Generic domain resolution (KYC / GROUP / TREASURY).
            active_generic = [d for d in candidates if d.lifecycle_status in _ACTIVE_LIFECYCLES]
            # KYC dossiers default CURRENT even without markers.
            if domain is AuthorityDomain.KYC_RELATIONSHIPS and not active_generic:
                active_generic = [
                    d
                    for d in candidates
                    if d.lifecycle_status
                    in {DocumentLifecycleStatus.CURRENT, DocumentLifecycleStatus.UNKNOWN}
                    and d.document_type is DocumentType.KYC_DOSSIER
                ]
            # Consolidated/group reports often lack explicit FINAL markers.
            if domain is AuthorityDomain.GROUP_STRUCTURE and not active_generic:
                active_generic = [
                    d
                    for d in candidates
                    if d.document_type is DocumentType.GROUP_OR_CONSOLIDATED_REPORT
                    and d.lifecycle_status
                    not in {
                        DocumentLifecycleStatus.SUPERSEDED,
                        DocumentLifecycleStatus.OBSOLETE,
                        DocumentLifecycleStatus.EXPIRED,
                        DocumentLifecycleStatus.DRAFT,
                        DocumentLifecycleStatus.PRELIMINARY,
                        DocumentLifecycleStatus.WORKING_PAPER,
                    }
                ]
            rejected_generic = [d for d in candidates if d.lifecycle_status in _REJECT_LIFECYCLES]
            if not active_generic:
                if domain in _REQUIRED_DOMAINS:
                    decisions.append(
                        AuthorityDecision(
                            decision_id=deterministic_id(
                                "authority-decision-v1",
                                scenario_id,
                                domain.value,
                                "missing",
                            ),
                            scenario_id=scenario_id,
                            domain=domain,
                            status=AuthorityStatus.MISSING_AUTHORITY,
                            rule_id="RULE_MISSING_AUTHORITY",
                            reason=f"no active candidate for {domain.value}",
                            rejected_document_ids=tuple(
                                sorted(d.document_id for d in rejected_generic)
                            ),
                            candidate_document_ids=tuple(sorted(c.document_id for c in candidates)),
                        )
                    )
                continue
            if len(active_generic) > 1:
                conflict = AuthorityConflict(
                    conflict_id=deterministic_id(
                        "authority-conflict-v1",
                        scenario_id,
                        domain.value,
                        *sorted(d.document_id for d in active_generic),
                    ),
                    scenario_id=scenario_id,
                    domain=domain,
                    candidate_document_ids=tuple(sorted(d.document_id for d in active_generic)),
                    reason=f"multiple active candidates for {domain.value}",
                )
                conflicts.append(conflict)
                decisions.append(
                    AuthorityDecision(
                        decision_id=deterministic_id(
                            "authority-decision-v1",
                            scenario_id,
                            domain.value,
                            "unresolved",
                        ),
                        scenario_id=scenario_id,
                        domain=domain,
                        status=AuthorityStatus.UNRESOLVED,
                        rule_id="RULE_EQUAL_AUTHORITATIVE_CONFLICT",
                        reason=conflict.reason,
                        rejected_document_ids=tuple(
                            sorted(d.document_id for d in rejected_generic)
                        ),
                        candidate_document_ids=tuple(sorted(c.document_id for c in candidates)),
                    )
                )
                continue
            winner = active_generic[0]
            decisions.append(
                AuthorityDecision(
                    decision_id=deterministic_id(
                        "authority-decision-v1",
                        scenario_id,
                        domain.value,
                        winner.document_id,
                    ),
                    scenario_id=scenario_id,
                    domain=domain,
                    status=AuthorityStatus.AUTHORITATIVE,
                    rule_id=f"RULE_{domain.value}_ACTIVE",
                    reason=f"single active document for {domain.value}",
                    winning_document_ids=(winner.document_id,),
                    rejected_document_ids=tuple(sorted(d.document_id for d in rejected_generic)),
                    candidate_document_ids=tuple(sorted(c.document_id for c in candidates)),
                    evidence_span_ids=winner.evidence_span_ids,
                )
            )

    decisions_sorted = tuple(
        sorted(decisions, key=lambda d: (d.scenario_id, d.domain.value, d.decision_id))
    )
    conflicts_sorted = tuple(
        sorted(conflicts, key=lambda c: (c.scenario_id, c.domain.value, c.conflict_id))
    )
    return decisions_sorted, conflicts_sorted
