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
        _row(3, "REV-P1-0007", "ACC-7801"),
        _row(4, "TXN-9999-0008", "ACC-7801"),
        _row(5, "TXN-ZZ-0009", "ACC-9999"),
    )
    bundle = route_transactions(rows, scenario_ids=frozenset({"P1"}))
    by_txn = {link.txn_id: link for link in bundle.links}
    assert by_txn["TXN-P1-0001"].method is ResolutionMethod.TXN_ID_PREFIX
    for txn in ("txn-p1-0003", "TXNP10005", "REV-P1-0007", "TXN-9999-0008"):
        assert by_txn[txn].scenario_id == "P1"
        assert by_txn[txn].method is ResolutionMethod.ACCOUNT_ID_FALLBACK
    assert by_txn["TXN-ZZ-0009"].scenario_id is None
    assert bundle.account_fallback_count == 4


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


def test_public_style_sequence_only_txn_id_still_routes() -> None:
    """A. Historical `TXN-<scenario>-<seq>` shape must not regress."""
    bundle = route_transactions(
        (_row(0, "TXN-S1-001", "ACC-7001"),),
        scenario_ids=frozenset({"S1"}),
    )
    assert bundle.links[0].scenario_id == "S1"
    assert bundle.links[0].method is ResolutionMethod.TXN_ID_PREFIX
    assert bundle.txn_id_linked_count == 1


def test_tagged_txn_id_recovers_scenario() -> None:
    """B. A semantic tag segment between scenario and sequence is opaque."""
    rows = (
        _row(0, "TXN-KC-CAP-29", "TELE-4471"),
        _row(1, "TXN-KC-FIN-05", "TELE-4471"),
        _row(2, "TXN-KC-REV-27", "TELE-4471"),
    )
    bundle = route_transactions(rows, scenario_ids=frozenset({"KC"}))
    assert [link.scenario_id for link in bundle.links] == ["KC", "KC", "KC"]
    assert {link.method for link in bundle.links} == {ResolutionMethod.TXN_ID_PREFIX}
    assert bundle.scenario_accounts["KC"] == frozenset({"TELE-4471"})


def test_multiple_tag_segments_still_recover_scenario() -> None:
    """C. Arbitrarily many opaque middle segments are tolerated."""
    bundle = route_transactions(
        (_row(0, "TXN-Q7-CAP-EXT-PHASE2-0031", "SATCOM-X91"),),
        scenario_ids=frozenset({"Q7"}),
    )
    assert bundle.links[0].scenario_id == "Q7"
    assert bundle.links[0].method is ResolutionMethod.TXN_ID_PREFIX
    assert bundle.scenario_accounts["Q7"] == frozenset({"SATCOM-X91"})


def test_tagged_noise_family_not_assigned_to_target() -> None:
    """D. A well-shaped token outside the template universe stays non-target."""
    bundle = route_transactions(
        (
            _row(0, "TXN-9170-CAP-29", "ACC-9170"),
            _row(1, "TXN-9170-0002", "ACC-9170"),
        ),
        scenario_ids=frozenset({"KC"}),
    )
    assert [link.scenario_id for link in bundle.links] == [None, None]
    assert {link.method for link in bundle.links} == {ResolutionMethod.UNRESOLVED}
    assert bundle.scenario_accounts == {}


def test_prefix_collision_between_scenario_tokens() -> None:
    """E. `K` and `KC` are distinct identities — no startswith() leakage."""
    rows = (
        _row(0, "TXN-K-0001", "ACC-1000"),
        _row(1, "TXN-KC-CAP-29", "TELE-4471"),
        _row(2, "TXN-KC2-CAP-30", "ACC-2000"),
        _row(3, "TXN-KCX-0004", "ACC-3000"),
    )
    bundle = route_transactions(rows, scenario_ids=frozenset({"K", "KC"}))
    by_txn = {link.txn_id: link for link in bundle.links}
    assert by_txn["TXN-K-0001"].scenario_id == "K"
    assert by_txn["TXN-KC-CAP-29"].scenario_id == "KC"
    assert by_txn["TXN-KC2-CAP-30"].scenario_id is None
    assert by_txn["TXN-KCX-0004"].scenario_id is None
    assert bundle.scenario_accounts["K"] == frozenset({"ACC-1000"})
    assert bundle.scenario_accounts["KC"] == frozenset({"TELE-4471"})


def test_opaque_account_identifier_anchors_scenario() -> None:
    """F. A non-ACC account identifier participates without special-casing."""
    rows = (
        _row(0, "TXN-Z9-0001", "SATCOM-X91"),
        _row(1, "unparseable-id-2", "SATCOM-X91"),
    )
    bundle = route_transactions(rows, scenario_ids=frozenset({"Z9"}))
    by_txn = {link.txn_id: link for link in bundle.links}
    assert by_txn["TXN-Z9-0001"].method is ResolutionMethod.TXN_ID_PREFIX
    assert by_txn["unparseable-id-2"].scenario_id == "Z9"
    assert by_txn["unparseable-id-2"].method is ResolutionMethod.ACCOUNT_ID_FALLBACK
    assert bundle.observed_account_ids == frozenset({"SATCOM-X91"})


def test_account_fallback_requires_unambiguous_ownership() -> None:
    """G/H. Fallback rescues only when the exact account has one owner."""
    rows = (
        _row(0, "TXN-P1-0001", "ACC-7801"),
        _row(1, "TXN-P2-0001", "ACC-7802"),
        # Shared account strongly claimed by two scenarios.
        _row(2, "TXN-P1-0002", "ACC-7900"),
        _row(3, "TXN-P2-0002", "ACC-7900"),
        # Malformed rows: rescued on the unambiguous account, blocked on the shared one.
        _row(4, "bad-row-a", "ACC-7801"),
        _row(5, "bad-row-b", "ACC-7900"),
    )
    bundle = route_transactions(rows, scenario_ids=frozenset({"P1", "P2"}))
    by_txn = {link.txn_id: link for link in bundle.links}
    assert by_txn["bad-row-a"].scenario_id == "P1"
    assert by_txn["bad-row-a"].method is ResolutionMethod.ACCOUNT_ID_FALLBACK
    assert by_txn["bad-row-b"].scenario_id is None
    assert by_txn["TXN-P1-0002"].method is ResolutionMethod.TXN_ID_ACCOUNT_CONFLICT
    assert by_txn["TXN-P2-0002"].method is ResolutionMethod.TXN_ID_ACCOUNT_CONFLICT
    assert any(c.kind.value == "TRANSACTION_ACCOUNT_CONFLICT" for c in bundle.conflicts)


def test_noise_families_do_not_change_target_totals() -> None:
    """I. Hundreds of decoy families leave target scenario totals untouched."""
    universe = frozenset({"S1", "KC"})
    targets = [
        _row(0, "TXN-S1-001", "ACC-7001"),
        _row(1, "TXN-S1-002", "ACC-7001"),
        _row(2, "TXN-KC-CAP-29", "TELE-4471"),
        _row(3, "TXN-KC-FIN-05", "TELE-4471"),
    ]
    baseline = route_transactions(tuple(targets), scenario_ids=universe)

    noisy = list(targets)
    index = len(noisy)
    for family in range(300):
        for seq in range(2):
            noisy.append(_row(index, f"TXN-9{family:03d}-CAP-{seq:02d}", f"ACC-9{family:03d}"))
            index += 1
    polluted = route_transactions(tuple(noisy), scenario_ids=universe)

    def totals(bundle: object) -> dict[str, int]:
        counts: dict[str, int] = {}
        for link in bundle.links:  # type: ignore[attr-defined]
            if link.scenario_id:
                counts[link.scenario_id] = counts.get(link.scenario_id, 0) + 1
        return counts

    assert totals(polluted) == totals(baseline) == {"S1": 2, "KC": 2}
    assert polluted.scenario_accounts == baseline.scenario_accounts
    assert polluted.unresolved_transaction_count == 600
    assert polluted.conflicts == ()


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
