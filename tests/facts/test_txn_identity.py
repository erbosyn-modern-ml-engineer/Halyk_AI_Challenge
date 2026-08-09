"""Trusted-ledger txn-ID vocabulary contract for Stage 5E fact extraction."""

from __future__ import annotations

from decimal import Decimal

from halyk_agent.domain.authority.models import AuthorityDomain
from halyk_agent.domain.fact_extraction.engine import run_fact_extraction
from halyk_agent.domain.fact_extraction.extractors import extract_candidates
from halyk_agent.domain.fact_extraction.models import (
    AmountCorrectionPayload,
    FactKind,
    PeriodDisposition,
    ReclassificationDisposition,
    TransactionPeriodPayload,
    TransactionReclassificationPayload,
)
from halyk_agent.domain.fact_extraction.txn_identity import (
    build_txn_id_vocabulary,
    find_txn_ids,
    is_complete_token_span,
)
from tests.authority.helpers import make_document
from tests.facts.helpers import (
    make_decision,
    make_definition,
    make_ledger_rows,
    make_requirement,
    reclass_modifier,
)


def test_public_style_ids_locate_from_vocabulary() -> None:
    vocab = build_txn_id_vocabulary(["TXN-S1-001", "TXN-B4-0026"])
    assert find_txn_ids("See TXN-S1-001 and TXN-B4-0026.", vocab) == (
        "TXN-S1-001",
        "TXN-B4-0026",
    )


def test_tagged_ids_locate_from_vocabulary() -> None:
    vocab = build_txn_id_vocabulary(["TXN-KC-CAP-29", "TXN-KC-FIN-05", "TXN-KC-REV-27"])
    text = "Операции TXN-KC-CAP-29, TXN-KC-FIN-05 и TXN-KC-REV-27 рассмотрены."
    assert find_txn_ids(text, vocab) == (
        "TXN-KC-CAP-29",
        "TXN-KC-FIN-05",
        "TXN-KC-REV-27",
    )


def test_multi_segment_opaque_ids() -> None:
    vocab = build_txn_id_vocabulary(["TXN-ZQ-LEASE-ADJ-17", "TXN-Q7-CAP-EXT-PHASE2-0031"])
    text = "TXN-ZQ-LEASE-ADJ-17 / TXN-Q7-CAP-EXT-PHASE2-0031"
    assert find_txn_ids(text, vocab) == (
        "TXN-ZQ-LEASE-ADJ-17",
        "TXN-Q7-CAP-EXT-PHASE2-0031",
    )


def test_exact_vocabulary_matching_only() -> None:
    vocab = build_txn_id_vocabulary(["TXN-KC-CAP-29"])
    assert find_txn_ids("mention TXN-KC-CAP-29 here", vocab) == ("TXN-KC-CAP-29",)
    assert find_txn_ids("mention TXN-KC-FIN-05 here", vocab) == ()
    assert find_txn_ids("mention TXN-KC-CAP-29 here", None) == ()
    assert find_txn_ids("mention TXN-KC-CAP-29 here", frozenset()) == ()


def test_boundary_collisions_do_not_partial_match() -> None:
    vocab = build_txn_id_vocabulary(["TXN-KC-CAP-29"])
    assert find_txn_ids("XTXN-KC-CAP-29", vocab) == ()
    assert find_txn_ids("TXN-KC-CAP-290", vocab) == ()
    assert find_txn_ids("TXN-KC-CAP-29-FAKE", vocab) == ()
    assert not is_complete_token_span("TXN-KC-CAP-29-FAKE", 0, len("TXN-KC-CAP-29"))

    longer = build_txn_id_vocabulary(["TXN-KC-CAP-29", "TXN-KC-CAP-29-FAKE"])
    assert find_txn_ids("prefix TXN-KC-CAP-29-FAKE suffix", longer) == ("TXN-KC-CAP-29-FAKE",)
    assert find_txn_ids("prefix TXN-KC-CAP-29 suffix", longer) == ("TXN-KC-CAP-29",)


def test_fake_document_txn_ids_absent_from_ledger_are_ignored() -> None:
    vocab = build_txn_id_vocabulary(["TXN-KC-CAP-29"])
    text = (
        "Операция TXN-FAKE-999 исключена из ковенантного периода 2025 года. "
        "Операция TXN-KC-CAP-29 исключена из ковенантного периода 2025 года."
    )
    doc = make_document(raw_text=text)
    cands = extract_candidates(
        make_requirement(FactKind.TRANSACTION_PERIOD, "TXN-"),
        doc,
        ledger_txn_ids=vocab,
    )
    assert len(cands) == 1
    payload = cands[0].payload
    assert isinstance(payload, TransactionPeriodPayload)
    assert payload.transaction_id == "TXN-KC-CAP-29"


def test_fact_attachment_to_tagged_transaction_id() -> None:
    text = (
        "(8.1) Операция TXN-KC-CAP-29 (Bridgeport Realty): сумма не отражена в выгрузке "
        "реестра; фактическая сумма операции составляет $138,011.66 (расход)."
    )
    doc = make_document(raw_text=text)
    cands = extract_candidates(
        make_requirement(FactKind.AMOUNT_CORRECTION, "TXN-"),
        doc,
        ledger_txn_ids={"TXN-KC-CAP-29"},
    )
    assert cands
    payload = cands[0].payload
    assert isinstance(payload, AmountCorrectionPayload)
    assert payload.transaction_id == "TXN-KC-CAP-29"
    assert payload.amount.value == Decimal("138011.66")


def test_existing_public_style_fact_extraction_behavior() -> None:
    period_text = (
        "(9.1) Операция TXN-B4-0026, датированная 2025-11-20, исключена из ковенантного периода "
        "2025 года."
    )
    doc = make_document(raw_text=period_text)
    period = extract_candidates(
        make_requirement(FactKind.TRANSACTION_PERIOD, "TXN-"),
        doc,
        ledger_txn_ids={"TXN-B4-0026"},
    )
    assert period
    assert isinstance(period[0].payload, TransactionPeriodPayload)
    assert period[0].payload.transaction_id == "TXN-B4-0026"
    assert period[0].payload.disposition is PeriodDisposition.EXCLUDE_FROM_PERIOD

    reclass_text = (
        "(7.2) Операция TXN-S1-001, первоначально учтённая как Операционные расходы "
        "($118,447.52), рассматривалась на предмет возможной переклассификации как "
        "Страховые премии; по итогам разъяснений руководства первоначальная классификация "
        "(Операционные расходы) сохраняется, и корректировка для целей ковенантов "
        "не производилась. Основание: рассмотрено и отклонено."
    )
    doc2 = make_document(raw_text=reclass_text)
    report = run_fact_extraction(
        definitions=(make_definition(modifiers=(reclass_modifier(),)),),
        decisions=(
            make_decision(
                domain=AuthorityDomain.FINANCIAL_ADJUSTMENTS,
                winning=(doc2.document_id,),
            ),
        ),
        documents=(doc2,),
        ledger_rows=make_ledger_rows("TXN-S1-001"),
    )
    rejected = [
        f.payload
        for f in report.accepted_facts
        if isinstance(f.payload, TransactionReclassificationPayload)
        and f.payload.disposition is ReclassificationDisposition.REJECTED
    ]
    assert rejected
    assert rejected[0].transaction_id == "TXN-S1-001"


def test_document_cannot_invent_txn_identity_without_ledger() -> None:
    text = "Операция TXN-KC-CAP-29 исключена из ковенантного периода 2025 года."
    doc = make_document(raw_text=text)
    assert (
        extract_candidates(
            make_requirement(FactKind.TRANSACTION_PERIOD, "TXN-"),
            doc,
            ledger_txn_ids=None,
        )
        == []
    )
