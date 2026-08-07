"""Pure deterministic scenario/entity routing engine (Stage 5B.1)."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from halyk_agent.domain.evidence import EvidenceSpan
from halyk_agent.domain.ids import deterministic_id, sha256_text
from halyk_agent.domain.parsing import CanonicalDocument
from halyk_agent.domain.routing.accounts import extract_account_identities
from halyk_agent.domain.routing.borrowers import extract_borrower_declarations
from halyk_agent.domain.routing.counterparties import build_counterparty_identities
from halyk_agent.domain.routing.documents import route_documents
from halyk_agent.domain.routing.models import (
    NORMALIZATION_VERSION,
    ROUTING_ALGORITHM_VERSION,
    ROUTING_SCHEMA_VERSION,
    AccountIdentity,
    BorrowerIdentity,
    CompanyAlias,
    ConflictKind,
    DiagnosticCode,
    DiagnosticSeverity,
    EntityResolutionConflict,
    IdentityEvidenceAssertion,
    LedgerRow,
    ResolutionMethod,
    RoutingDiagnostic,
    RoutingManifest,
    RoutingReport,
    ScenarioIdentity,
    ScenarioRoutingRecord,
    TxnIdParserConfig,
)
from halyk_agent.domain.routing.normalize import normalize_legal_name_keys
from halyk_agent.domain.routing.scenarios import (
    ScenarioDiscoveryError,
    discover_scenarios,
    scenario_universe,
    template_cell_count,
)
from halyk_agent.domain.routing.transactions import route_transactions


def hash_dataset_manifest(manifest_payload: Mapping[str, Any]) -> str:
    text = json.dumps(manifest_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_text(text)


def hash_canonical_documents(documents: tuple[CanonicalDocument, ...]) -> str:
    parts = [
        f"{doc.document_id}:{doc.document_version_id}:{doc.source_sha256}"
        for doc in sorted(documents, key=lambda d: (d.document_id, d.document_version_id))
    ]
    return sha256_text("\n".join(parts))


def hash_evidence_catalogue(spans: tuple[EvidenceSpan, ...] | None) -> str:
    if not spans:
        return sha256_text("")
    parts = [span.id for span in sorted(spans, key=lambda item: item.id)]
    return sha256_text("\n".join(parts))


def _anchor_borrowers(
    borrowers: tuple[BorrowerIdentity, ...],
    *,
    scenario_accounts: dict[str, frozenset[str]],
) -> tuple[
    tuple[BorrowerIdentity, ...],
    dict[str, frozenset[str]],
    dict[str, tuple[str, ...]],
    tuple[EntityResolutionConflict, ...],
    tuple[RoutingDiagnostic, ...],
    tuple[CompanyAlias, ...],
]:
    account_to_scenario: dict[str, str] = {}
    for mapped_scenario_id, accounts in scenario_accounts.items():
        for account_id in accounts:
            account_to_scenario[account_id] = mapped_scenario_id

    updated: list[BorrowerIdentity] = []
    aliases: list[CompanyAlias] = []
    by_account_keys: dict[str, set[str]] = defaultdict(set)
    by_account_raw: dict[str, set[str]] = defaultdict(set)
    conflicts: list[EntityResolutionConflict] = []
    diagnostics: list[RoutingDiagnostic] = []
    raw_by_identity: dict[str, set[str]] = defaultdict(set)

    for borrower in borrowers:
        keys = normalize_legal_name_keys(borrower.legal_name_raw)
        for alias in keys.aliases:
            aliases.append(
                alias.model_copy(
                    update={
                        "source_document_id": borrower.document_id,
                        "evidence_span_id": borrower.evidence_span_id,
                    }
                )
            )
        raw_by_identity[keys.identity_key].add(borrower.legal_name_raw)
        scenario_id: str | None = None
        anchored = False
        if borrower.account_id_normalized and borrower.account_id_normalized in account_to_scenario:
            scenario_id = account_to_scenario[borrower.account_id_normalized]
            anchored = True
            by_account_keys[borrower.account_id_normalized].add(keys.identity_key)
            by_account_raw[borrower.account_id_normalized].add(borrower.legal_name_raw)
        updated.append(
            borrower.model_copy(
                update={
                    "scenario_id": scenario_id,
                    "anchored": anchored,
                    "normalized_name": keys.identity_key,
                    "identity_key": keys.identity_key,
                    "base_key": keys.base_key,
                }
            )
        )

    for account_id, names in sorted(by_account_keys.items()):
        if len(names) > 1:
            conflict_scenario: str | None = account_to_scenario.get(account_id)
            conflicts.append(
                EntityResolutionConflict(
                    conflict_id=deterministic_id(
                        "borrower-name-conflict-v1",
                        account_id,
                        *sorted(names),
                    ),
                    kind=ConflictKind.BORROWER_NAME_CONFLICT,
                    severity=DiagnosticSeverity.WARNING,
                    scenario_ids=(conflict_scenario,) if conflict_scenario else (),
                    account_ids=(account_id,),
                    detail=(
                        "multiple borrower legal names declared for the same account; "
                        "authority deferred to Stage 5C"
                    ),
                )
            )
            diagnostics.append(
                RoutingDiagnostic(
                    code=DiagnosticCode.BORROWER_NAME_CONFLICT,
                    severity=DiagnosticSeverity.WARNING,
                    message=(
                        f"account {account_id} has conflicting borrower names: "
                        f"{', '.join(sorted(names))}"
                    ),
                    scenario_id=conflict_scenario,
                    account_id=account_id,
                )
            )

    borrower_identity_by_scenario: dict[str, set[str]] = defaultdict(set)
    for account_id, names in by_account_keys.items():
        mapped_scenario: str | None = account_to_scenario.get(account_id)
        if mapped_scenario is None:
            continue
        borrower_identity_by_scenario[mapped_scenario].update(names)

    return (
        tuple(updated),
        {k: frozenset(v) for k, v in borrower_identity_by_scenario.items()},
        {k: tuple(sorted(v)) for k, v in raw_by_identity.items()},
        tuple(conflicts),
        tuple(diagnostics),
        tuple(aliases),
    )


def _build_identity_evidence(
    *,
    document_links: tuple[Any, ...],
    borrowers: tuple[BorrowerIdentity, ...],
    account_extractions: tuple[AccountIdentity, ...],
    spans_by_id: dict[str, EvidenceSpan],
    documents_by_id: dict[str, CanonicalDocument],
) -> tuple[IdentityEvidenceAssertion, ...]:
    assertions: list[IdentityEvidenceAssertion] = []
    for link in document_links:
        if link.method in {
            ResolutionMethod.UNRESOLVED,
        }:
            continue
        for span_id in link.evidence_span_ids:
            span = spans_by_id.get(span_id)
            if span is None:
                continue
            doc = documents_by_id.get(link.document_id)
            identity_type = {
                ResolutionMethod.EXPLICIT_ACCOUNT_ID: "ACCOUNT_ID",
                ResolutionMethod.EXPLICIT_BORROWER_DECLARATION: "BORROWER_DECLARATION",
                ResolutionMethod.GROUP_SEGMENT_DECLARATION: "GROUP_SEGMENT_DECLARATION",
                ResolutionMethod.DECLARED_ENTITY_RELATION: "DECLARED_ENTITY_RELATION",
                ResolutionMethod.NORMALIZED_LEGAL_NAME: "NORMALIZED_LEGAL_NAME",
            }.get(link.method, link.method.value)
            scenario_id = link.scenario_ids[0] if len(link.scenario_ids) == 1 else None
            assertions.append(
                IdentityEvidenceAssertion(
                    assertion_id=deterministic_id(
                        "identity-assertion-v1",
                        link.document_id,
                        span_id,
                        link.method.value,
                    ),
                    scenario_id=scenario_id,
                    document_id=link.document_id,
                    identity_type=identity_type,
                    resolution_method=link.method,
                    evidence_span_id=span_id,
                    raw_quote=span.quote,
                    page_number=span.page_number,
                    text_origin=span.text_origin.value,
                    source_sha256=doc.source_sha256 if doc else None,
                    ocr_backend_identity=span.ocr_backend_identity,
                )
            )
    for borrower in borrowers:
        span = spans_by_id.get(borrower.evidence_span_id)
        if span is None:
            continue
        doc = documents_by_id.get(borrower.document_id)
        assertions.append(
            IdentityEvidenceAssertion(
                assertion_id=deterministic_id(
                    "identity-assertion-v1",
                    borrower.document_id,
                    borrower.evidence_span_id,
                    "BORROWER_CANDIDATE",
                ),
                scenario_id=borrower.scenario_id,
                document_id=borrower.document_id,
                identity_type="BORROWER_CANDIDATE",
                resolution_method=(
                    ResolutionMethod.EXPLICIT_BORROWER_DECLARATION
                    if borrower.anchored
                    else ResolutionMethod.UNRESOLVED
                ),
                evidence_span_id=borrower.evidence_span_id,
                raw_quote=span.quote,
                page_number=span.page_number,
                text_origin=span.text_origin.value,
                source_sha256=doc.source_sha256 if doc else None,
                ocr_backend_identity=span.ocr_backend_identity,
            )
        )
    for account in account_extractions:
        if not account.evidence_span_id:
            continue
        span = spans_by_id.get(account.evidence_span_id)
        if span is None:
            continue
        assertions.append(
            IdentityEvidenceAssertion(
                assertion_id=deterministic_id(
                    "identity-assertion-v1",
                    account.document_id or "",
                    account.evidence_span_id,
                    "ACCOUNT_EXTRACTION",
                ),
                document_id=account.document_id,
                identity_type="ACCOUNT_ID",
                resolution_method=ResolutionMethod.EXPLICIT_ACCOUNT_ID,
                evidence_span_id=account.evidence_span_id,
                raw_quote=span.quote,
                page_number=span.page_number,
                text_origin=span.text_origin.value,
                ocr_backend_identity=span.ocr_backend_identity,
            )
        )
    assertions.sort(key=lambda item: item.assertion_id)
    # Deduplicate by assertion_id
    dedup: dict[str, IdentityEvidenceAssertion] = {}
    for item in assertions:
        dedup[item.assertion_id] = item
    return tuple(sorted(dedup.values(), key=lambda item: item.assertion_id))


def run_routing(
    *,
    template_answers: Mapping[str, Any],
    ledger_rows: tuple[LedgerRow, ...],
    documents: tuple[CanonicalDocument, ...],
    evidence_catalogue: tuple[EvidenceSpan, ...] | None = None,
    dataset_manifest_payload: Mapping[str, Any],
    txn_id_parser: TxnIdParserConfig | None = None,
    parsed_input_identity: Mapping[str, Any] | None = None,
) -> RoutingReport:
    scenarios = discover_scenarios(template_answers)
    universe = scenario_universe(scenarios)
    cell_count = template_cell_count(scenarios)

    txn_bundle = route_transactions(
        ledger_rows,
        scenario_ids=universe,
        parser_config=txn_id_parser,
    )

    diagnostics: list[RoutingDiagnostic] = list(txn_bundle.diagnostics)
    conflicts: list[EntityResolutionConflict] = list(txn_bundle.conflicts)

    for scenario in scenarios:
        accounts = txn_bundle.scenario_accounts.get(scenario.scenario_id, frozenset())
        if not accounts:
            diagnostics.append(
                RoutingDiagnostic(
                    code=DiagnosticCode.SCENARIO_WITHOUT_ACCOUNT,
                    severity=DiagnosticSeverity.ERROR,
                    message=f"scenario {scenario.scenario_id} has no linked ledger account",
                    scenario_id=scenario.scenario_id,
                )
            )
            conflicts.append(
                EntityResolutionConflict(
                    conflict_id=deterministic_id(
                        "scenario-without-account-v1",
                        scenario.scenario_id,
                    ),
                    kind=ConflictKind.SCENARIO_WITHOUT_ACCOUNT,
                    severity=DiagnosticSeverity.ERROR,
                    scenario_ids=(scenario.scenario_id,),
                    detail="no ledger account observed for scenario",
                )
            )

    account_extractions: list[AccountIdentity] = []
    identity_spans: list[EvidenceSpan] = []
    all_borrowers: list[BorrowerIdentity] = []
    documents_by_id = {doc.document_id: doc for doc in documents}
    for document in sorted(documents, key=lambda d: d.document_id):
        acc_bundle = extract_account_identities(document)
        account_extractions.extend(acc_bundle.accounts)
        identity_spans.extend(acc_bundle.spans)
        diagnostics.extend(acc_bundle.diagnostics)
        conflicts.extend(acc_bundle.conflicts)
        borrow_bundle = extract_borrower_declarations(document)
        all_borrowers.extend(borrow_bundle.borrowers)
        identity_spans.extend(borrow_bundle.spans)

    (
        anchored_borrowers,
        borrower_identities,
        raw_by_identity,
        borrower_conflicts,
        borrower_diags,
        aliases,
    ) = _anchor_borrowers(
        tuple(all_borrowers),
        scenario_accounts=txn_bundle.scenario_accounts,
    )
    conflicts.extend(borrower_conflicts)
    diagnostics.extend(borrower_diags)

    doc_bundle = route_documents(
        documents,
        account_extractions=tuple(account_extractions),
        borrowers=anchored_borrowers,
        scenario_accounts=txn_bundle.scenario_accounts,
        borrower_identity_by_scenario=borrower_identities,
        borrower_raw_by_identity=raw_by_identity,
    )
    conflicts.extend(doc_bundle.conflicts)
    diagnostics.extend(doc_bundle.diagnostics)
    identity_spans.extend(doc_bundle.spans)

    counterparties = build_counterparty_identities(txn_bundle.links)

    docs_by_scenario: dict[str, set[str]] = defaultdict(set)
    for doc_link in doc_bundle.links:
        for scenario_id in doc_link.scenario_ids:
            docs_by_scenario[scenario_id].add(doc_link.document_id)

    txn_count_by_scenario: dict[str, int] = defaultdict(int)
    for txn_link in txn_bundle.links:
        if txn_link.scenario_id:
            txn_count_by_scenario[txn_link.scenario_id] += 1

    scenario_routes: list[ScenarioRoutingRecord] = []
    for scenario in scenarios:
        route_accounts = tuple(
            sorted(txn_bundle.scenario_accounts.get(scenario.scenario_id, frozenset()))
        )
        scenario_routes.append(
            ScenarioRoutingRecord(
                scenario_id=scenario.scenario_id,
                ordinal=scenario.ordinal,
                required_covenant_ids=scenario.required_covenant_ids,
                account_ids=route_accounts,
                transaction_count=txn_count_by_scenario.get(scenario.scenario_id, 0),
                document_ids=tuple(sorted(docs_by_scenario.get(scenario.scenario_id, set()))),
                borrower_normalized_names=tuple(
                    sorted(borrower_identities.get(scenario.scenario_id, frozenset()))
                ),
            )
        )

    spans_by_id = {span.id: span for span in identity_spans}
    identity_evidence = _build_identity_evidence(
        document_links=doc_bundle.links,
        borrowers=anchored_borrowers,
        account_extractions=tuple(account_extractions),
        spans_by_id=spans_by_id,
        documents_by_id=documents_by_id,
    )

    conflicts_sorted = tuple(
        sorted(conflicts, key=lambda item: (item.kind.value, item.conflict_id))
    )
    diagnostics_sorted = tuple(
        sorted(
            diagnostics,
            key=lambda item: (
                item.code.value,
                item.scenario_id or "",
                item.document_id or "",
                item.txn_id or "",
                item.message,
            ),
        )
    )

    evidence_hash = ""
    if parsed_input_identity and parsed_input_identity.get("evidence_catalogue_sha256"):
        evidence_hash = str(parsed_input_identity["evidence_catalogue_sha256"])
    else:
        evidence_hash = hash_evidence_catalogue(evidence_catalogue)

    parsed_identity_payload = dict(parsed_input_identity or {})
    if (
        evidence_catalogue is not None
        and "evidence_catalogue_sha256" not in parsed_identity_payload
    ):
        parsed_identity_payload["evidence_catalogue_sha256"] = evidence_hash

    manifest = RoutingManifest(
        schema_version=ROUTING_SCHEMA_VERSION,
        dataset_manifest_hash=hash_dataset_manifest(dataset_manifest_payload),
        canonical_documents_hash=hash_canonical_documents(documents),
        evidence_catalogue_hash=evidence_hash,
        parsed_input_identity=parsed_identity_payload,
        routing_algorithm_version=ROUTING_ALGORITHM_VERSION,
        normalization_version=NORMALIZATION_VERSION,
        scenario_count=len(scenarios),
        resolved_document_count=doc_bundle.resolved_count,
        unresolved_document_count=doc_bundle.unresolved_count,
        transaction_link_count=len(txn_bundle.links),
        conflict_count=len(conflicts_sorted),
        template_cell_count=cell_count,
        ledger_row_count=len(ledger_rows),
        scenario_transaction_count=txn_bundle.scenario_transaction_count,
        txn_id_linked_count=txn_bundle.txn_id_linked_count,
        account_fallback_count=txn_bundle.account_fallback_count,
        multi_scenario_document_count=doc_bundle.multi_scenario_count,
        identity_evidence_count=len(identity_evidence),
    )

    return RoutingReport(
        manifest=manifest,
        scenarios=scenarios,
        scenario_routes=tuple(scenario_routes),
        transaction_links=txn_bundle.links,
        document_links=doc_bundle.links,
        borrowers=anchored_borrowers,
        aliases=aliases,
        counterparties=counterparties,
        conflicts=conflicts_sorted,
        diagnostics=diagnostics_sorted,
        account_extractions=tuple(
            sorted(
                account_extractions,
                key=lambda a: (
                    a.account_id_normalized,
                    a.document_id or "",
                    a.evidence_span_id or "",
                ),
            )
        ),
        identity_evidence=identity_evidence,
        identity_spans=tuple(sorted(identity_spans, key=lambda s: s.id)),
    )


class RoutingEngineError(Exception):
    def __init__(self, message: str, *, code: str = "ROUTING_ERROR") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def safe_discover_or_raise(template_answers: Mapping[str, Any]) -> tuple[ScenarioIdentity, ...]:
    try:
        return discover_scenarios(template_answers)
    except ScenarioDiscoveryError as exc:
        raise RoutingEngineError(str(exc), code="SCENARIO_DISCOVERY") from exc
