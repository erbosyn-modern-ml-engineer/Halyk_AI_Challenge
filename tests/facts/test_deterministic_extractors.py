"""Deterministic extractor tests with RU/EN fixture documents."""

from __future__ import annotations

from decimal import Decimal

from halyk_agent.domain.authority.models import AuthorityDomain
from halyk_agent.domain.fact_extraction.extractors import extract_candidates
from halyk_agent.domain.fact_extraction.models import (
    FactKind,
    FactRequirement,
    OffLedgerAmountPayload,
    OwnershipPayload,
    ReclassificationDisposition,
    RelatedPartyThresholdPayload,
    TransactionReclassificationPayload,
)
from tests.authority.helpers import make_document


def _req(kind: FactKind, domain: AuthorityDomain, *cues: str) -> FactRequirement:
    return FactRequirement(
        requirement_id=f"req-{kind.value}",
        scenario_id="S1",
        fact_kind=kind,
        authority_domain=domain,
        reason_code="TEST",
        lexical_cues=cues,
    )


def test_reclassification_ru_pattern() -> None:
    text = (
        "(4.1) Сумма в размере $592,296.10, выплаченная контрагенту Irtysh Advisory Bureau, "
        "первоначально учтённая как Консультационные услуги, переклассифицирована для целей "
        "соблюдения ковенантов как Процентные расходы."
    )
    doc = make_document(raw_text=text)
    cands = extract_candidates(
        _req(
            FactKind.TRANSACTION_RECLASSIFICATION,
            AuthorityDomain.FINANCIAL_ADJUSTMENTS,
            "перекласс",
        ),
        doc,
    )
    assert len(cands) >= 1
    payload = cands[0].payload
    assert isinstance(payload, TransactionReclassificationPayload)
    assert payload.amount is not None
    assert payload.amount.value == Decimal("592296.10")
    assert payload.amount.currency == "USD"
    assert "Irtysh" in (payload.counterparty or "")
    assert "Процентн" in payload.to_category
    assert "Консультационн" in payload.from_category
    assert payload.disposition is ReclassificationDisposition.ACCEPTED


def test_period_exclude_and_amount_missing_ledger() -> None:
    from halyk_agent.domain.fact_extraction.models import (
        AmountCorrectionPayload,
        PeriodDisposition,
        TransactionPeriodPayload,
    )

    period_text = (
        "(9.1) Операция TXN-B4-0026, датированная 2025-11-20, исключена из ковенантного периода "
        "2025 года."
    )
    doc = make_document(raw_text=period_text)
    period = extract_candidates(
        _req(FactKind.TRANSACTION_PERIOD, AuthorityDomain.FINANCIAL_ADJUSTMENTS, "TXN-"),
        doc,
    )
    assert period
    assert isinstance(period[0].payload, TransactionPeriodPayload)
    assert period[0].payload.transaction_id == "TXN-B4-0026"
    assert period[0].payload.disposition is PeriodDisposition.EXCLUDE_FROM_PERIOD

    amount_text = (
        "(8.1) Операция TXN-P8-0031 (Kyzylorda Drilling Personnel LLP): "
        "сумма не отражена в выгрузке "
        "реестра; фактическая сумма операции составляет $884,204.16 (расход)."
    )
    doc2 = make_document(raw_text=amount_text)
    amounts = extract_candidates(
        _req(FactKind.AMOUNT_CORRECTION, AuthorityDomain.FINANCIAL_ADJUSTMENTS, "TXN-"),
        doc2,
    )
    assert amounts
    assert isinstance(amounts[0].payload, AmountCorrectionPayload)
    assert amounts[0].payload.transaction_id == "TXN-P8-0031"
    assert amounts[0].payload.amount.value == Decimal("884204.16")


def test_rejected_reclassification() -> None:
    text = (
        "Сумма в размере $10,000.00, выплаченная контрагенту Acme LLP, "
        "учтенная как OPEX, переклассифицирована как CAPEX была отклонена аудитором."
    )
    doc = make_document(raw_text=text)
    cands = extract_candidates(
        _req(
            FactKind.TRANSACTION_RECLASSIFICATION,
            AuthorityDomain.FINANCIAL_ADJUSTMENTS,
            "перекласс",
        ),
        doc,
    )
    assert cands
    assert isinstance(cands[0].payload, TransactionReclassificationPayload)
    assert cands[0].payload.disposition is ReclassificationDisposition.REJECTED


def test_ownership_and_kyc_threshold() -> None:
    text = (
        "Бенефициарное владение и контроль\n"
        "Организация Доля голосующих прав\n"
        "Ertis Capital, LLP 31.4%\n"
        "Irtysh Advisory Bureau 18.6%\n"
        "Организации, в которых Группа владеет 20.0% и более голосующих прав, "
        "признаются связанными сторонами для целей Договора."
    )
    doc = make_document(raw_text=text)
    own = extract_candidates(
        _req(FactKind.OWNERSHIP, AuthorityDomain.KYC_RELATIONSHIPS, "владе", "%"),
        doc,
    )
    assert any(
        isinstance(c.payload, OwnershipPayload)
        and c.payload.entity_name.startswith("Ertis")
        and c.payload.ownership_percent == Decimal("31.4")
        for c in own
    )
    thr = extract_candidates(
        _req(
            FactKind.RELATED_PARTY_THRESHOLD,
            AuthorityDomain.KYC_RELATIONSHIPS,
            "связанн",
        ),
        doc,
    )
    assert thr
    assert isinstance(thr[0].payload, RelatedPartyThresholdPayload)
    assert thr[0].payload.threshold_percent == Decimal("20.0")


def test_severance_off_ledger() -> None:
    text = (
        "совокупное обязательство по программе выходных пособий в размере $918,447.52 "
        "раскрывается вне основной книги для целей ковенантов."
    )
    doc = make_document(raw_text=text)
    cands = extract_candidates(
        _req(
            FactKind.OFF_LEDGER_AMOUNT,
            AuthorityDomain.FINANCIAL_ADJUSTMENTS,
            "выходн",
        ),
        doc,
    )
    assert cands
    assert isinstance(cands[0].payload, OffLedgerAmountPayload)
    assert cands[0].payload.amount.value == Decimal("918447.52")
