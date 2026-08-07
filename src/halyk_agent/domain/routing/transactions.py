"""Deterministic transaction ↔ scenario routing from the ledger."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from halyk_agent.domain.ids import deterministic_id
from halyk_agent.domain.routing.models import (
    ConflictKind,
    DiagnosticCode,
    DiagnosticSeverity,
    EntityResolutionConflict,
    LedgerRow,
    ResolutionConfidence,
    ResolutionMethod,
    RoutingDiagnostic,
    TransactionEntityLink,
    TxnIdParserConfig,
)
from halyk_agent.domain.routing.normalize import normalize_account_id


@dataclass(frozen=True, slots=True)
class TransactionRoutingBundle:
    links: tuple[TransactionEntityLink, ...]
    scenario_accounts: dict[str, frozenset[str]]
    diagnostics: tuple[RoutingDiagnostic, ...]
    conflicts: tuple[EntityResolutionConflict, ...]
    duplicate_txn_ids: tuple[str, ...]
    scenario_transaction_count: int
    unresolved_transaction_count: int


def route_transactions(
    rows: tuple[LedgerRow, ...],
    *,
    scenario_ids: frozenset[str],
    parser_config: TxnIdParserConfig | None = None,
) -> TransactionRoutingBundle:
    """
    Map ledger rows to scenarios via configurable txn-id token extraction.

    Proposed tokens must validate against the authoritative scenario universe.
    """
    config = parser_config or TxnIdParserConfig()
    pattern = re.compile(config.pattern)

    links: list[TransactionEntityLink] = []
    diagnostics: list[RoutingDiagnostic] = []
    conflicts: list[EntityResolutionConflict] = []
    scenario_accounts: dict[str, set[str]] = defaultdict(set)
    seen_txn: dict[str, int] = {}
    duplicates: list[str] = []
    scenario_txn_count = 0
    unresolved_count = 0

    for row in rows:
        txn_id = row.txn_id.strip()
        account_norm = normalize_account_id(row.account_id)
        if txn_id in seen_txn:
            duplicates.append(txn_id)
            diagnostics.append(
                RoutingDiagnostic(
                    code=DiagnosticCode.TRANSACTION_ACCOUNT_CONFLICT,
                    severity=DiagnosticSeverity.ERROR,
                    message=f"duplicate txn_id rejected for second occurrence: {txn_id}",
                    txn_id=txn_id,
                    account_id=account_norm,
                )
            )
            conflicts.append(
                EntityResolutionConflict(
                    conflict_id=deterministic_id("dup-txn-v1", txn_id, row.row_index),
                    kind=ConflictKind.DUPLICATE_TXN_ID,
                    severity=DiagnosticSeverity.ERROR,
                    txn_ids=(txn_id,),
                    account_ids=(account_norm,),
                    detail=f"duplicate txn_id at rows {seen_txn[txn_id]} and {row.row_index}",
                )
            )
            continue
        seen_txn[txn_id] = row.row_index

        match = pattern.match(txn_id)
        token: str | None = None
        scenario_id: str | None = None
        method = ResolutionMethod.UNRESOLVED
        confidence = ResolutionConfidence.UNRESOLVED

        if match is not None:
            token = match.group("scenario")
            if token in scenario_ids:
                scenario_id = token
                method = ResolutionMethod.TXN_ID_PREFIX
                confidence = ResolutionConfidence.EXACT
                scenario_accounts[scenario_id].add(account_norm)
                scenario_txn_count += 1
            else:
                unresolved_count += 1
                diagnostics.append(
                    RoutingDiagnostic(
                        code=DiagnosticCode.TRANSACTION_UNKNOWN_SCENARIO,
                        severity=DiagnosticSeverity.INFO,
                        message=(f"txn token {token!r} is not in the template scenario universe"),
                        txn_id=txn_id,
                        account_id=account_norm,
                    )
                )
        else:
            unresolved_count += 1
            diagnostics.append(
                RoutingDiagnostic(
                    code=DiagnosticCode.TRANSACTION_UNKNOWN_SCENARIO,
                    severity=DiagnosticSeverity.INFO,
                    message=f"txn_id did not match configured pattern: {txn_id}",
                    txn_id=txn_id,
                    account_id=account_norm,
                )
            )

        links.append(
            TransactionEntityLink(
                txn_id=txn_id,
                row_index=row.row_index,
                ledger_source_file=row.ledger_source_file,
                account_id_raw=row.account_id,
                account_id_normalized=account_norm,
                scenario_id=scenario_id,
                scenario_token=token,
                method=method,
                confidence=confidence,
                counterparty_raw=row.counterparty,
            )
        )

    # Account consistency: for each scenario, detect multi-account sets.
    frozen_accounts = {
        scenario: frozenset(accounts) for scenario, accounts in scenario_accounts.items()
    }
    for scenario, accounts in sorted(frozen_accounts.items()):
        if len(accounts) > 1:
            diagnostics.append(
                RoutingDiagnostic(
                    code=DiagnosticCode.SCENARIO_WITH_MULTIPLE_ACCOUNTS,
                    severity=DiagnosticSeverity.WARNING,
                    message=(
                        f"scenario {scenario} maps to multiple accounts: "
                        f"{', '.join(sorted(accounts))}"
                    ),
                    scenario_id=scenario,
                )
            )
            conflicts.append(
                EntityResolutionConflict(
                    conflict_id=deterministic_id(
                        "scenario-multi-account-v1",
                        scenario,
                        *sorted(accounts),
                    ),
                    kind=ConflictKind.SCENARIO_WITH_MULTIPLE_ACCOUNTS,
                    severity=DiagnosticSeverity.WARNING,
                    scenario_ids=(scenario,),
                    account_ids=tuple(sorted(accounts)),
                    detail="scenario linked to multiple ledger account identifiers",
                )
            )

    # Detect txn-level account disagreement vs other txns of same scenario
    # when a scenario has a unique primary account and a txn disagrees.
    primary = {
        scenario: next(iter(accounts))
        for scenario, accounts in frozen_accounts.items()
        if len(accounts) == 1
    }
    for link in links:
        if link.scenario_id is None:
            continue
        expected = primary.get(link.scenario_id)
        if expected is None:
            continue
        if link.account_id_normalized != expected:
            diagnostics.append(
                RoutingDiagnostic(
                    code=DiagnosticCode.TRANSACTION_ACCOUNT_CONFLICT,
                    severity=DiagnosticSeverity.ERROR,
                    message=(
                        f"txn {link.txn_id} account {link.account_id_normalized} "
                        f"conflicts with scenario {link.scenario_id} account {expected}"
                    ),
                    scenario_id=link.scenario_id,
                    txn_id=link.txn_id,
                    account_id=link.account_id_normalized,
                )
            )
            conflicts.append(
                EntityResolutionConflict(
                    conflict_id=deterministic_id(
                        "txn-account-conflict-v1",
                        link.txn_id,
                        link.account_id_normalized,
                        expected,
                    ),
                    kind=ConflictKind.TRANSACTION_ACCOUNT_CONFLICT,
                    severity=DiagnosticSeverity.ERROR,
                    scenario_ids=(link.scenario_id,),
                    account_ids=(link.account_id_normalized, expected),
                    txn_ids=(link.txn_id,),
                    detail="txn account disagrees with scenario account set",
                )
            )

    links.sort(key=lambda item: (item.row_index, item.txn_id))
    return TransactionRoutingBundle(
        links=tuple(links),
        scenario_accounts=frozen_accounts,
        diagnostics=tuple(diagnostics),
        conflicts=tuple(conflicts),
        duplicate_txn_ids=tuple(sorted(set(duplicates))),
        scenario_transaction_count=scenario_txn_count,
        unresolved_transaction_count=unresolved_count,
    )
