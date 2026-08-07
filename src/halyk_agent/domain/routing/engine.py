"""Pure deterministic scenario/entity routing engine (Stage 5B)."""

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
    LedgerRow,
    RoutingDiagnostic,
    RoutingManifest,
    RoutingReport,
    ScenarioIdentity,
    ScenarioRoutingRecord,
    TxnIdParserConfig,
)
from halyk_agent.domain.routing.normalize import normalize_legal_name
from halyk_agent.domain.routing.scenarios import (
    ScenarioDiscoveryError,
    discover_scenarios,
    scenario_universe,
    template_cell_count,
)
from halyk_agent.domain.routing.transactions import route_transactions


def hash_dataset_manifest(manifest_payload: Mapping[str, Any]) -> str:
    """Deterministic hash of sanitized manifest JSON (sorted keys, no timestamps)."""
    text = json.dumps(manifest_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_text(text)


def hash_canonical_documents(documents: tuple[CanonicalDocument, ...]) -> str:
    parts = [
        f"{doc.document_id}:{doc.document_version_id}:{doc.source_sha256}"
        for doc in sorted(documents, key=lambda d: (d.document_id, d.document_version_id))
    ]
    return sha256_text("\n".join(parts))


def _anchor_borrowers(
    borrowers: tuple[BorrowerIdentity, ...],
    *,
    scenario_accounts: dict[str, frozenset[str]],
) -> tuple[
    tuple[BorrowerIdentity, ...],
    dict[str, frozenset[str]],
    tuple[EntityResolutionConflict, ...],
    tuple[RoutingDiagnostic, ...],
    tuple[CompanyAlias, ...],
]:
    account_to_scenario: dict[str, str] = {}
    for mapped_scenario_id, accounts in scenario_accounts.items():
        for account_id in accounts:
            # Prefer unique mapping; multi-account scenarios still map each account.
            account_to_scenario[account_id] = mapped_scenario_id

    anchored: list[BorrowerIdentity] = []
    aliases: list[CompanyAlias] = []
    by_account_names: dict[str, set[str]] = defaultdict(set)
    by_account_raw: dict[str, set[str]] = defaultdict(set)
    conflicts: list[EntityResolutionConflict] = []
    diagnostics: list[RoutingDiagnostic] = []

    for borrower in borrowers:
        _, _, alias_tuple = normalize_legal_name(borrower.legal_name_raw)
        for alias in alias_tuple:
            aliases.append(
                alias.model_copy(
                    update={
                        "source_document_id": borrower.document_id,
                        "evidence_span_id": borrower.evidence_span_id,
                    }
                )
            )
        scenario_id: str | None = None
        if borrower.account_id_normalized and borrower.account_id_normalized in account_to_scenario:
            scenario_id = account_to_scenario[borrower.account_id_normalized]
            by_account_names[borrower.account_id_normalized].add(borrower.normalized_name)
            by_account_raw[borrower.account_id_normalized].add(borrower.legal_name_raw)
        anchored.append(borrower.model_copy(update={"scenario_id": scenario_id}))

    # Borrower name conflicts per account (do not choose authority).
    for account_id, names in sorted(by_account_names.items()):
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

    borrower_name_by_scenario: dict[str, set[str]] = defaultdict(set)
    for account_id, names in by_account_names.items():
        mapped_scenario: str | None = account_to_scenario.get(account_id)
        if mapped_scenario is None:
            continue
        borrower_name_by_scenario[mapped_scenario].update(names)

    return (
        tuple(anchored),
        {k: frozenset(v) for k, v in borrower_name_by_scenario.items()},
        tuple(conflicts),
        tuple(diagnostics),
        tuple(aliases),
    )


def run_routing(
    *,
    template_answers: Mapping[str, Any],
    ledger_rows: tuple[LedgerRow, ...],
    documents: tuple[CanonicalDocument, ...],
    evidence_catalogue: tuple[EvidenceSpan, ...] | None = None,
    dataset_manifest_payload: Mapping[str, Any],
    txn_id_parser: TxnIdParserConfig | None = None,
) -> RoutingReport:
    """
    Deterministic routing pipeline.

    Does not accept a raw dataset root. Evidence catalogue is accepted for
    lineage consumption; identity spans are still created from canonical text.
    """
    _ = evidence_catalogue  # consumed at app boundary for I/O/validation; engine is span-producing
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

    # Scenarios without accounts
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
    for document in sorted(documents, key=lambda d: d.document_id):
        acc_bundle = extract_account_identities(document)
        account_extractions.extend(acc_bundle.accounts)
        identity_spans.extend(acc_bundle.spans)
        diagnostics.extend(acc_bundle.diagnostics)
        conflicts.extend(acc_bundle.conflicts)
        borrow_bundle = extract_borrower_declarations(document)
        all_borrowers.extend(borrow_bundle.borrowers)
        identity_spans.extend(borrow_bundle.spans)
    _ = identity_spans  # evidence lineage produced; catalogue merge reserved

    anchored_borrowers, borrower_names, borrower_conflicts, borrower_diags, aliases = (
        _anchor_borrowers(
            tuple(all_borrowers),
            scenario_accounts=txn_bundle.scenario_accounts,
        )
    )
    conflicts.extend(borrower_conflicts)
    diagnostics.extend(borrower_diags)

    doc_bundle = route_documents(
        documents,
        account_extractions=tuple(account_extractions),
        borrowers=anchored_borrowers,
        scenario_accounts=txn_bundle.scenario_accounts,
        borrower_name_by_scenario=borrower_names,
    )
    conflicts.extend(doc_bundle.conflicts)
    diagnostics.extend(doc_bundle.diagnostics)

    counterparties = build_counterparty_identities(txn_bundle.links)

    # Build per-scenario routes
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
                    sorted(borrower_names.get(scenario.scenario_id, frozenset()))
                ),
            )
        )

    # Deterministic conflict / diagnostic ordering
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

    manifest = RoutingManifest(
        schema_version=ROUTING_SCHEMA_VERSION,
        dataset_manifest_hash=hash_dataset_manifest(dataset_manifest_payload),
        canonical_documents_hash=hash_canonical_documents(documents),
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
        multi_scenario_document_count=doc_bundle.multi_scenario_count,
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
    )


class RoutingEngineError(Exception):
    """Application-facing routing error wrapper for discovery failures."""

    def __init__(self, message: str, *, code: str = "ROUTING_ERROR") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def safe_discover_or_raise(template_answers: Mapping[str, Any]) -> tuple[ScenarioIdentity, ...]:
    try:
        return discover_scenarios(template_answers)
    except ScenarioDiscoveryError as exc:
        raise RoutingEngineError(str(exc), code="SCENARIO_DISCOVERY") from exc
