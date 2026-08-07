"""Identity evidence provenance (Stage 5B.2)."""

# ruff: noqa: RUF001

from __future__ import annotations

from halyk_agent.domain.routing.engine import run_routing
from halyk_agent.domain.routing.models import LedgerRow, ResolutionMethod
from tests.routing.helpers import make_document


def test_document_identity_assertions_have_source_sha() -> None:
    doc = make_document(
        artifact="ev",
        raw_text=(
            "Alpha Energy JSC (joint stock company under Kazakhstan law) "
            "(далее — «Заёмщик»), имеющим банковский счёт ACC-7801 у Кредитора."
        ),
    )
    report = run_routing(
        template_answers={"P1": {"6.1": None}},
        ledger_rows=(
            LedgerRow(
                row_index=0,
                txn_id="TXN-P1-0001",
                date="2025-01-01",
                account_id="ACC-7801",
                counterparty="A",
                description="",
                amount="1",
                currency="KZT",
                ledger_source_file="ledger.csv",
            ),
        ),
        documents=(doc,),
        dataset_manifest_payload={"schema_version": "test"},
        ledger_source_sha256="b" * 64,
    )
    doc_assertions = [a for a in report.identity_evidence if a.provenance_kind == "document_span"]
    assert doc_assertions
    assert all(a.source_sha256 == "a" * 64 for a in doc_assertions)
    assert all(a.evidence_span_id and a.raw_quote for a in doc_assertions)


def test_txn_prefix_and_fallback_assertions_auditable() -> None:
    doc = make_document(artifact="noise", raw_text="parking policy")
    report = run_routing(
        template_answers={"P1": {"6.1": None}},
        ledger_rows=(
            LedgerRow(
                row_index=0,
                txn_id="TXN-P1-0001",
                date="2025-01-01",
                account_id="ACC-7801",
                counterparty="A",
                description="",
                amount="1",
                currency="KZT",
                ledger_source_file="ledger.csv",
            ),
            LedgerRow(
                row_index=1,
                txn_id="REV-P1-0002",
                date="2025-01-02",
                account_id="ACC-7801",
                counterparty="B",
                description="",
                amount="2",
                currency="KZT",
                ledger_source_file="ledger.csv",
            ),
        ),
        documents=(doc,),
        dataset_manifest_payload={"schema_version": "test"},
        ledger_source_sha256="c" * 64,
    )
    by_method = {}
    for assertion in report.identity_evidence:
        if assertion.provenance_kind != "ledger_row":
            continue
        by_method.setdefault(assertion.resolution_method, []).append(assertion)

    prefix = by_method[ResolutionMethod.TXN_ID_PREFIX]
    fallback = by_method[ResolutionMethod.ACCOUNT_ID_FALLBACK]
    assert len(prefix) == 1
    assert prefix[0].txn_id == "TXN-P1-0001"
    assert prefix[0].account_id == "ACC-7801"
    assert prefix[0].ledger_source_file == "ledger.csv"
    assert prefix[0].ledger_row_index == 0
    assert prefix[0].source_sha256 == "c" * 64
    assert prefix[0].evidence_span_id is None

    assert len(fallback) == 1
    assert fallback[0].txn_id == "REV-P1-0002"
    assert fallback[0].resolution_method is ResolutionMethod.ACCOUNT_ID_FALLBACK
    assert fallback[0].ledger_row_index == 1
    assert fallback[0].source_sha256 == "c" * 64


def test_identity_evidence_byte_identical() -> None:
    doc = make_document(
        artifact="d",
        raw_text="Banking account ACC-7801 for borrower party.",
    )
    rows = (
        LedgerRow(
            row_index=0,
            txn_id="TXN-P1-0001",
            date="2025-01-01",
            account_id="ACC-7801",
            counterparty="X",
            description="",
            amount="1",
            currency="KZT",
            ledger_source_file="ledger.csv",
        ),
    )
    kwargs = dict(
        template_answers={"P1": {"6.1": None}},
        ledger_rows=rows,
        documents=(doc,),
        dataset_manifest_payload={"schema_version": "test"},
        ledger_source_sha256="d" * 64,
    )
    a = run_routing(**kwargs)
    b = run_routing(**kwargs)
    dump_a = "\n".join(x.model_dump_json() for x in a.identity_evidence)
    dump_b = "\n".join(x.model_dump_json() for x in b.identity_evidence)
    assert dump_a == dump_b
