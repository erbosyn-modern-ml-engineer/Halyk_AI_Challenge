"""Determinism and end-to-end routing smoke (synthetic)."""

from __future__ import annotations

from halyk_agent.domain.routing.engine import run_routing
from halyk_agent.domain.routing.models import LedgerRow
from tests.routing.helpers import make_document


def _report(docs_order: tuple[str, ...]):
    docs = tuple(
        make_document(
            artifact=f"a{i}",
            source_file=f"{name}.pdf",
            raw_text=f"Banking account ACC-7801 for borrower party {name}.",
        )
        for i, name in enumerate(docs_order)
    )
    rows = (
        LedgerRow(
            row_index=0,
            txn_id="TXN-P1-0001",
            date="2025-01-01",
            account_id="ACC-7801",
            counterparty="Northwind Catering",
            description="",
            amount="10",
            currency="KZT",
            ledger_source_file="ledger.csv",
        ),
        LedgerRow(
            row_index=1,
            txn_id="TXN-9001-0001",
            date="2025-01-02",
            account_id="ACC-9001",
            counterparty="Noise Co",
            description="",
            amount="5",
            currency="KZT",
            ledger_source_file="ledger.csv",
        ),
    )
    return run_routing(
        template_answers={"P1": {"6.1": None, "6.2": None, "6.3": None}},
        ledger_rows=rows,
        documents=docs,
        dataset_manifest_payload={"schema_version": "test", "primary": "ledger"},
    )


def test_same_input_byte_identical_manifest() -> None:
    a = _report(("doc_b", "doc_a"))
    b = _report(("doc_a", "doc_b"))
    assert a.manifest.model_dump_json() == b.manifest.model_dump_json()
    assert a.manifest.dataset_manifest_hash == b.manifest.dataset_manifest_hash
    assert [d.document_id for d in a.document_links] == [d.document_id for d in b.document_links]


def test_no_cross_scenario_contamination_from_near_names() -> None:
    services = make_document(
        artifact="svc",
        raw_text=(
            "Shymkent Refinery Services JSC (акционерное общество) "
            "(далее — «Заёмщик»), имеющим банковский счёт ACC-7803."
        ),
    )
    plain = make_document(
        artifact="pln",
        raw_text=(
            "Shymkent Refinery JSC (акционерное общество) "
            "(далее — «Заёмщик»), имеющим банковский счёт ACC-7204."
        ),
    )
    report = run_routing(
        template_answers={"P3": {"6.1": None}, "B4": {"6.1": None}},
        ledger_rows=(
            LedgerRow(
                row_index=0,
                txn_id="TXN-P3-0001",
                date="2025-01-01",
                account_id="ACC-7803",
                counterparty="X",
                description="",
                amount="1",
                currency="KZT",
                ledger_source_file="l.csv",
            ),
            LedgerRow(
                row_index=1,
                txn_id="TXN-B4-0001",
                date="2025-01-01",
                account_id="ACC-7204",
                counterparty="Y",
                description="",
                amount="1",
                currency="KZT",
                ledger_source_file="l.csv",
            ),
        ),
        documents=(services, plain),
        dataset_manifest_payload={"k": "v"},
    )
    by_doc = {link.document_id: link for link in report.document_links}
    assert by_doc[services.document_id].scenario_ids == ("P3",)
    assert by_doc[plain.document_id].scenario_ids == ("B4",)
