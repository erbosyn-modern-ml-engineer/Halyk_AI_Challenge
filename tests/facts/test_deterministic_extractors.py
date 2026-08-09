"""Deterministic extractor tests with RU/EN fixture documents."""

# ruff: noqa: RUF001

from __future__ import annotations

from decimal import Decimal

from halyk_agent.domain.authority.models import AuthorityDomain
from halyk_agent.domain.fact_extraction.extractors import extract_candidates
from halyk_agent.domain.fact_extraction.models import (
    FactKind,
    OffLedgerAmountPayload,
    OwnershipPayload,
    RateSource,
    ReclassificationDisposition,
    RelatedPartyThresholdPayload,
    TransactionReclassificationPayload,
)
from tests.authority.helpers import make_document
from tests.facts.helpers import make_requirement


def test_reclassification_ru_pattern() -> None:
    text = (
        "(4.1) Сумма в размере $592,296.10, выплаченная контрагенту Irtysh Advisory Bureau, "
        "первоначально учтённая как Консультационные услуги, переклассифицирована для целей "
        "соблюдения ковенантов как Процентные расходы."
    )
    doc = make_document(raw_text=text)
    cands = extract_candidates(
        make_requirement(
            FactKind.TRANSACTION_RECLASSIFICATION,
            "перекласс",
            domain=AuthorityDomain.FINANCIAL_ADJUSTMENTS,
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
        make_requirement(FactKind.TRANSACTION_PERIOD, "TXN-"),
        doc,
        ledger_txn_ids={"TXN-B4-0026"},
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
        make_requirement(FactKind.AMOUNT_CORRECTION, "TXN-"),
        doc2,
        ledger_txn_ids={"TXN-P8-0031"},
    )
    assert amounts
    assert isinstance(amounts[0].payload, AmountCorrectionPayload)
    assert amounts[0].payload.transaction_id == "TXN-P8-0031"
    assert amounts[0].payload.amount.value == Decimal("884204.16")


def test_period_preserves_service_dates() -> None:
    from datetime import date

    from halyk_agent.domain.fact_extraction.models import TransactionPeriodPayload

    text = (
        "Операция TXN-B4-0001 относится к услугам, оказанным в период с 2026-01-15 по 2026-03-20."
    )
    doc = make_document(raw_text=text)
    cands = extract_candidates(
        make_requirement(FactKind.TRANSACTION_PERIOD, "TXN-"),
        doc,
        ledger_txn_ids={"TXN-B4-0001"},
    )
    assert cands
    payload = cands[0].payload
    assert isinstance(payload, TransactionPeriodPayload)
    assert payload.service_start == date(2026, 1, 15)
    assert payload.service_end == date(2026, 3, 20)


def test_fx_settlement_no_calculated_rate() -> None:
    from halyk_agent.domain.fact_extraction.models import FxRatePayload

    text = "Счёт на сумму 100 EUR урегулирован в размере $116.00."
    doc = make_document(raw_text=text)
    cands = extract_candidates(make_requirement(FactKind.FX_RATE, "курс", "eur"), doc)
    assert cands
    payload = cands[0].payload
    assert isinstance(payload, FxRatePayload)
    assert payload.rate_source is RateSource.NOT_STATED
    assert payload.explicit_rate is None
    assert payload.source_amount is not None
    assert payload.source_amount.value == Decimal("100")
    assert payload.settlement_amount is not None
    assert payload.settlement_amount.value == Decimal("116.00")


def test_fx_explicit_rate_kept() -> None:
    from halyk_agent.domain.fact_extraction.models import FxRatePayload

    text = "обменный курс составил 1.16 EUR/USD"
    doc = make_document(raw_text=text)
    cands = extract_candidates(make_requirement(FactKind.FX_RATE, "курс"), doc)
    assert cands
    payload = cands[0].payload
    assert isinstance(payload, FxRatePayload)
    assert payload.rate_source is RateSource.EXPLICIT
    assert payload.explicit_rate == Decimal("1.16")


def test_rejected_reclassification() -> None:
    text = (
        "Сумма в размере $10,000.00, выплаченная контрагенту Acme LLP, "
        "учтенная как OPEX, переклассифицирована как CAPEX была отклонена аудитором."
    )
    doc = make_document(raw_text=text)
    cands = extract_candidates(
        make_requirement(
            FactKind.TRANSACTION_RECLASSIFICATION,
            "перекласс",
            domain=AuthorityDomain.FINANCIAL_ADJUSTMENTS,
        ),
        doc,
    )
    assert cands
    assert isinstance(cands[0].payload, TransactionReclassificationPayload)
    assert cands[0].payload.disposition is ReclassificationDisposition.REJECTED


def test_ownership_rejects_legal_form_only() -> None:
    text = "Бенефициарное владение\nLLP 31.4%\nErtis Capital, LLP 31.4%\n"
    doc = make_document(raw_text=text)
    own = extract_candidates(
        make_requirement(
            FactKind.OWNERSHIP, "владе", "%", domain=AuthorityDomain.KYC_RELATIONSHIPS
        ),
        doc,
    )
    names = [c.payload.entity_name for c in own if isinstance(c.payload, OwnershipPayload)]
    assert all(n.casefold() not in {"llp", "jsc", "тоо", "ао"} for n in names)
    assert any(n.startswith("Ertis") for n in names)


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
        make_requirement(
            FactKind.OWNERSHIP, "владе", "%", domain=AuthorityDomain.KYC_RELATIONSHIPS
        ),
        doc,
    )
    assert any(
        isinstance(c.payload, OwnershipPayload)
        and c.payload.entity_name.startswith("Ertis")
        and c.payload.ownership_percent == Decimal("31.4")
        for c in own
    )
    thr = extract_candidates(
        make_requirement(
            FactKind.RELATED_PARTY_THRESHOLD,
            "связанн",
            domain=AuthorityDomain.KYC_RELATIONSHIPS,
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
        make_requirement(FactKind.OFF_LEDGER_AMOUNT, "выходн"),
        doc,
    )
    assert cands
    assert isinstance(cands[0].payload, OffLedgerAmountPayload)
    assert cands[0].payload.amount.value == Decimal("918447.52")
