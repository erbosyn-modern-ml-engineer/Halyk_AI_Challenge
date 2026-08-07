"""Deterministic transaction ↔ scenario routing from the ledger (Stage 5B.1)."""

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
    txn_id_linked_count: int = 0
    account_fallback_count: int = 0


def route_transactions(
    rows: tuple[LedgerRow, ...],
    *,
    scenario_ids: frozenset[str],
    parser_config: TxnIdParserConfig | None = None,
) -> TransactionRoutingBundle:
    """
    Two-pass routing:

    1) Strong TXN_ID_PREFIX observations build scenario↔account anchors.
    2) Malformed/unknown txn-id rows fall back to ACCOUNT_ID_FALLBACK when the
       exact account belongs to exactly one anchored scenario.
    """
    config = parser_config or TxnIdParserConfig()
    pattern = re.compile(config.pattern)

    diagnostics: list[RoutingDiagnostic] = []
    conflicts: list[EntityResolutionConflict] = []
    scenario_accounts: dict[str, set[str]] = defaultdict(set)
    seen_txn: dict[str, int] = {}
    duplicates: list[str] = []

    # Pass 1 material: pending rows for fallback after anchors exist.
    pending: list[tuple[LedgerRow, str, str | None, bool]] = []
    # (row, account_norm, token_or_none, pattern_matched)

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
        if match is not None:
            token = match.group("scenario")
            if token in scenario_ids:
                scenario_accounts[token].add(account_norm)
                pending.append((row, account_norm, token, True))
            else:
                pending.append((row, account_norm, token, False))
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
            pending.append((row, account_norm, None, False))
            diagnostics.append(
                RoutingDiagnostic(
                    code=DiagnosticCode.ACCOUNT_WITHOUT_SCENARIO_TOKEN,
                    severity=DiagnosticSeverity.INFO,
                    message=f"txn_id did not match configured pattern: {txn_id}",
                    txn_id=txn_id,
                    account_id=account_norm,
                )
            )

    frozen_accounts = {
        scenario: frozenset(accounts) for scenario, accounts in scenario_accounts.items()
    }
    account_to_scenarios: dict[str, set[str]] = defaultdict(set)
    for scenario, accounts in frozen_accounts.items():
        for account in accounts:
            account_to_scenarios[account].add(scenario)

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

    # Detect accounts claimed by multiple distinct strong scenario tokens.
    account_strong_tokens: dict[str, set[str]] = defaultdict(set)
    for _row, account_norm, token, strong in pending:
        if strong and token is not None:
            account_strong_tokens[account_norm].add(token)

    links: list[TransactionEntityLink] = []
    scenario_txn_count = 0
    unresolved_count = 0
    txn_id_linked = 0
    account_fallback = 0

    for row, account_norm, token, strong in pending:
        txn_id = row.txn_id.strip()
        scenario_id: str | None = None
        method = ResolutionMethod.UNRESOLVED
        confidence = ResolutionConfidence.UNRESOLVED

        if strong and token is not None:
            claimed = account_strong_tokens.get(account_norm, set())
            if len(claimed) > 1:
                method = ResolutionMethod.TXN_ID_ACCOUNT_CONFLICT
                confidence = ResolutionConfidence.UNRESOLVED
                unresolved_count += 1
                diagnostics.append(
                    RoutingDiagnostic(
                        code=DiagnosticCode.TRANSACTION_ACCOUNT_CONFLICT,
                        severity=DiagnosticSeverity.ERROR,
                        message=(
                            f"txn {txn_id} account {account_norm} claimed by multiple "
                            f"scenario tokens: {', '.join(sorted(claimed))}"
                        ),
                        scenario_id=token,
                        txn_id=txn_id,
                        account_id=account_norm,
                    )
                )
                conflicts.append(
                    EntityResolutionConflict(
                        conflict_id=deterministic_id(
                            "txn-id-account-conflict-v1",
                            txn_id,
                            account_norm,
                            *sorted(claimed),
                        ),
                        kind=ConflictKind.TRANSACTION_ACCOUNT_CONFLICT,
                        severity=DiagnosticSeverity.ERROR,
                        scenario_ids=tuple(sorted(claimed)),
                        account_ids=(account_norm,),
                        txn_ids=(txn_id,),
                        detail="same account claimed by multiple txn-id scenario tokens",
                    )
                )
            else:
                scenario_id = token
                method = ResolutionMethod.TXN_ID_PREFIX
                confidence = ResolutionConfidence.EXACT
                scenario_txn_count += 1
                txn_id_linked += 1
        else:
            # Fallback: exact known account belonging to exactly one scenario.
            candidates = account_to_scenarios.get(account_norm, set())
            if len(candidates) == 1:
                scenario_id = next(iter(candidates))
                method = ResolutionMethod.ACCOUNT_ID_FALLBACK
                confidence = ResolutionConfidence.DERIVED
                scenario_txn_count += 1
                account_fallback += 1
            elif len(candidates) > 1:
                unresolved_count += 1
                diagnostics.append(
                    RoutingDiagnostic(
                        code=DiagnosticCode.TRANSACTION_ACCOUNT_CONFLICT,
                        severity=DiagnosticSeverity.ERROR,
                        message=(
                            f"txn {txn_id} account {account_norm} belongs to multiple scenarios"
                        ),
                        txn_id=txn_id,
                        account_id=account_norm,
                    )
                )
                conflicts.append(
                    EntityResolutionConflict(
                        conflict_id=deterministic_id(
                            "txn-account-multi-scenario-v1",
                            txn_id,
                            account_norm,
                            *sorted(candidates),
                        ),
                        kind=ConflictKind.TRANSACTION_ACCOUNT_CONFLICT,
                        severity=DiagnosticSeverity.ERROR,
                        scenario_ids=tuple(sorted(candidates)),
                        account_ids=(account_norm,),
                        txn_ids=(txn_id,),
                        detail="account belongs to multiple scenarios during fallback",
                    )
                )
            else:
                unresolved_count += 1

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

    # Within-scenario primary-account disagreement for strong links.
    primary = {
        scenario: next(iter(accounts))
        for scenario, accounts in frozen_accounts.items()
        if len(accounts) == 1
    }
    for link in links:
        if link.scenario_id is None or link.method is not ResolutionMethod.TXN_ID_PREFIX:
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
        txn_id_linked_count=txn_id_linked,
        account_fallback_count=account_fallback,
    )
