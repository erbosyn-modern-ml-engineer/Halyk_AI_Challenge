"""End-to-end routing generalization over identifier formatting (Stage 5B).

Reproduces the structural class of private-dataset bug without hardcoding any
private identifier as routing logic: a scenario whose transaction IDs carry an
extra semantic tag segment and whose account identifier does not follow the
historically observed ``ACC-<digits>`` shape must still route.
"""

from __future__ import annotations

from halyk_agent.domain.routing.engine import run_routing
from halyk_agent.domain.routing.models import LedgerRow, ResolutionMethod
from tests.routing.helpers import make_document


def _row(index: int, txn_id: str, account_id: str) -> LedgerRow:
    return LedgerRow(
        row_index=index,
        txn_id=txn_id,
        date="2025-01-01",
        account_id=account_id,
        counterparty="Vendor",
        description="",
        amount="1",
        currency="KZT",
        ledger_source_file="ledger.csv",
    )


def _run(*, template, rows, documents=()):
    return run_routing(
        template_answers=template,
        ledger_rows=rows,
        documents=documents,
        dataset_manifest_payload={"k": "v"},
    )


def test_tagged_ids_and_opaque_account_route_end_to_end() -> None:
    doc = make_document(
        artifact="opaque",
        raw_text="Настоящий отчёт относится к счёту SATCOM-X91 Заёмщика.",
    )
    report = _run(
        template={"S1": {"6.1": None}, "Q7": {"6.1": None}},
        rows=(
            _row(0, "TXN-S1-001", "ACC-7001"),
            _row(1, "TXN-S1-002", "ACC-7001"),
            _row(2, "TXN-Q7-CAP-29", "SATCOM-X91"),
            _row(3, "TXN-Q7-FIN-05", "SATCOM-X91"),
            _row(4, "TXN-Q7-REV-27-B", "SATCOM-X91"),
        ),
        documents=(doc,),
    )
    routes = {r.scenario_id: r for r in report.scenario_routes}
    assert routes["S1"].transaction_count == 2
    assert routes["S1"].account_ids == ("ACC-7001",)
    assert routes["Q7"].transaction_count == 3
    assert routes["Q7"].account_ids == ("SATCOM-X91",)

    # The opaque identifier carries the document to its scenario via exact evidence.
    assert routes["Q7"].document_ids == (doc.document_id,)
    link = next(item for item in report.document_links if item.document_id == doc.document_id)
    assert link.method is ResolutionMethod.EXPLICIT_ACCOUNT_ID
    assert link.account_ids == ("SATCOM-X91",)


def test_renaming_identifiers_does_not_require_code_changes() -> None:
    """Same semantic relationships, entirely different identifier vocabulary."""
    original = _run(
        template={"KC": {"6.1": None}},
        rows=(
            _row(0, "TXN-KC-CAP-29", "TELE-4471"),
            _row(1, "TXN-KC-FIN-05", "TELE-4471"),
        ),
    )
    renamed = _run(
        template={"ZQ": {"6.1": None}},
        rows=(
            _row(0, "TXN-ZQ-OPEX-77", "SATCOM-X91"),
            _row(1, "TXN-ZQ-MKT-01", "SATCOM-X91"),
        ),
    )
    assert original.scenario_routes[0].transaction_count == 2
    assert renamed.scenario_routes[0].transaction_count == 2
    assert renamed.scenario_routes[0].account_ids == ("SATCOM-X91",)
    assert original.manifest.txn_id_linked_count == renamed.manifest.txn_id_linked_count


def test_noise_scenarios_never_enter_the_target_universe() -> None:
    rows = [
        _row(0, "TXN-S1-001", "ACC-7001"),
        _row(1, "TXN-KC-CAP-29", "TELE-4471"),
    ]
    index = len(rows)
    for family in range(120):
        rows.append(_row(index, f"TXN-9{family:03d}-CAP-01", f"ACC-9{family:03d}"))
        index += 1
    report = _run(template={"S1": {"6.1": None}, "KC": {"6.1": None}}, rows=tuple(rows))

    counts = {r.scenario_id: r.transaction_count for r in report.scenario_routes}
    assert counts == {"S1": 1, "KC": 1}
    assert report.manifest.scenario_transaction_count == 2
    linked = [link for link in report.transaction_links if link.scenario_id]
    assert {link.txn_id for link in linked} == {"TXN-S1-001", "TXN-KC-CAP-29"}
