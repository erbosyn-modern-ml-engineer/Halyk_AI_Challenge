"""Transaction routing tests including account fallback (Stage 5B.1)."""

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


def test_unknown_token_unresolved_without_anchor() -> None:
    bundle = route_transactions(
        (_row(0, "TXN-9001-0036", "ACC-9001"),),
        scenario_ids=frozenset({"P1"}),
    )
    assert bundle.links[0].scenario_id is None
    assert bundle.links[0].method is ResolutionMethod.UNRESOLVED


def test_malformed_txn_known_account_fallback() -> None:
    rows = (
        _row(0, "TXN-P1-0001", "ACC-7801"),
        _row(1, "txn-p1-0003", "ACC-7801"),
        _row(2, "TXNP10005", "ACC-7801"),
        _row(3, "TXN-P1-0006-R", "ACC-7801"),
        _row(4, "REV-P1-0007", "ACC-7801"),
        _row(5, "TXN-9999-0008", "ACC-7801"),
        _row(6, "TXN-ZZ-0009", "ACC-9999"),
    )
    bundle = route_transactions(rows, scenario_ids=frozenset({"P1"}))
    by_txn = {link.txn_id: link for link in bundle.links}
    assert by_txn["TXN-P1-0001"].method is ResolutionMethod.TXN_ID_PREFIX
    for txn in ("txn-p1-0003", "TXNP10005", "TXN-P1-0006-R", "REV-P1-0007", "TXN-9999-0008"):
        assert by_txn[txn].scenario_id == "P1"
        assert by_txn[txn].method is ResolutionMethod.ACCOUNT_ID_FALLBACK
    assert by_txn["TXN-ZZ-0009"].scenario_id is None
    assert bundle.account_fallback_count == 5


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


def test_conflicting_txn_id_and_account() -> None:
    bundle = route_transactions(
        (
            _row(0, "TXN-P1-0001", "ACC-7801"),
            _row(1, "TXN-P2-0001", "ACC-7801"),
        ),
        scenario_ids=frozenset({"P1", "P2"}),
    )
    conflict_links = [
        link for link in bundle.links if link.method is ResolutionMethod.TXN_ID_ACCOUNT_CONFLICT
    ]
    assert conflict_links
    assert any(c.kind.value == "TRANSACTION_ACCOUNT_CONFLICT" for c in bundle.conflicts)
