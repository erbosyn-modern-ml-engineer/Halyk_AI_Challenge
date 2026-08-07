"""Borrower extraction and evidence lineage tests."""

# ruff: noqa: RUF001

from __future__ import annotations

from halyk_agent.domain.routing.borrowers import extract_borrower_declarations
from halyk_agent.domain.routing.engine import run_routing
from halyk_agent.domain.routing.models import LedgerRow
from tests.routing.helpers import make_document


def test_borrower_declaration_requires_evidence_span() -> None:
    text = (
        "Agreement between Ekibastuz Energy JSC (joint stock company under Kazakhstan law) "
        "(далее — «Заёмщик»), имеющим банковский счёт ACC-7201 у Кредитора."
    )
    doc = make_document(raw_text=text)
    bundle = extract_borrower_declarations(doc)
    assert bundle.borrowers
    assert all(b.evidence_span_id for b in bundle.borrowers)
    assert len(bundle.spans) == len(bundle.borrowers)
    assert bundle.borrowers[0].account_id_normalized == "ACC-7201"


def test_transaction_link_keeps_row_source() -> None:
    doc = make_document(raw_text="noise")
    report = run_routing(
        template_answers={"P1": {"6.1": None}},
        ledger_rows=(
            LedgerRow(
                row_index=7,
                txn_id="TXN-P1-0007",
                date="2025-01-01",
                account_id="ACC-7801",
                counterparty="Alpha",
                description="",
                amount="1",
                currency="KZT",
                ledger_source_file="master_ledger_2025.csv",
            ),
        ),
        documents=(doc,),
        dataset_manifest_payload={"schema_version": "test", "x": 1},
    )
    link = report.transaction_links[0]
    assert link.row_index == 7
    assert link.ledger_source_file == "master_ledger_2025.csv"
    assert link.scenario_id == "P1"
