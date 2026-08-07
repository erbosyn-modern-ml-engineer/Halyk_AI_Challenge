"""Transaction routing tests."""

from __future__ import annotations

from halyk_agent.domain.routing.models import LedgerRow, ResolutionMethod
from halyk_agent.domain.routing.transactions import route_transactions


def _row(index: int, txn_id: str, account_id: str) -> LedgerRow:
    return LedgerRow(
        row_index=index,
        txn_id=txn_id,
        date="2025-01-01",
        account_id=account_id,
        counterparty="Vendor",
        description="",
        amount="10.00",
        currency="KZT",
        ledger_source_file="ledger.csv",
    )


def test_txn_token_validated_against_universe() -> None:
    bundle = route_transactions(
        (_row(0, "TXN-P3-0024", "ACC-7803"),),
        scenario_ids=frozenset({"P3"}),
    )
    assert bundle.links[0].scenario_id == "P3"
    assert bundle.links[0].method is ResolutionMethod.TXN_ID_PREFIX
    assert bundle.scenario_accounts["P3"] == frozenset({"ACC-7803"})


def test_unknown_token_unresolved() -> None:
    bundle = route_transactions(
        (_row(0, "TXN-9001-0036", "ACC-9001"),),
        scenario_ids=frozenset({"P1"}),
    )
    assert bundle.links[0].scenario_id is None
    assert bundle.links[0].method is ResolutionMethod.UNRESOLVED
    assert bundle.unresolved_transaction_count == 1


def test_duplicate_txn_rejected() -> None:
    bundle = route_transactions(
        (
            _row(0, "TXN-P1-0001", "ACC-7801"),
            _row(1, "TXN-P1-0001", "ACC-7801"),
        ),
        scenario_ids=frozenset({"P1"}),
    )
    assert "TXN-P1-0001" in bundle.duplicate_txn_ids
    assert len(bundle.links) == 1


def test_multiple_account_conflict_represented() -> None:
    bundle = route_transactions(
        (
            _row(0, "TXN-P1-0001", "ACC-7801"),
            _row(1, "TXN-P1-0002", "ACC-7999"),
        ),
        scenario_ids=frozenset({"P1"}),
    )
    assert bundle.scenario_accounts["P1"] == frozenset({"ACC-7801", "ACC-7999"})
    assert any(c.kind.value == "SCENARIO_WITH_MULTIPLE_ACCOUNTS" for c in bundle.conflicts)
