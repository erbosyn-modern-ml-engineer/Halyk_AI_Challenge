"""Taxonomy classification tests (Stage 5C)."""

# ruff: noqa: RUF001

from __future__ import annotations

from halyk_agent.domain.authority.classify import classify_document
from halyk_agent.domain.authority.metadata import extract_metadata
from halyk_agent.domain.authority.models import (
    AuthorityDomain,
    DocumentLifecycleStatus,
    DocumentType,
)
from tests.authority.helpers import make_document, make_link


def _classify(text: str, *, group: bool = False):
    doc = make_document(raw_text=text)
    meta = extract_metadata(doc)
    link = make_link(doc, group_document=group)
    return classify_document(doc, metadata=meta, link=link)


def test_loan_agreement_classification() -> None:
    bundle = _classify(
        "Halyk Bank\nИСПОЛНИТЕЛЬНЫЙ ЭКЗЕМПЛЯР\nДОГОВОР БАНКОВСКОГО ЗАЙМА\n№ ACC-7801\n"
        "Financial covenants Article 6"
    )
    assert bundle.classification.document_type is DocumentType.LOAN_AGREEMENT
    assert bundle.classification.lifecycle_status is DocumentLifecycleStatus.CURRENT_EXECUTED
    assert AuthorityDomain.COVENANT_TERMS in bundle.classification.authority_domains
    assert bundle.classification.evidence_span_ids


def test_auditor_final_classification() -> None:
    bundle = _classify(
        "Turan Verity Audit LLP\nНезависимый аудитор Заёмщика\nАУДИТОРСКОЕ ДЕЛО №ACC-7801\n"
        "окончательный аудиторский отчет"
    )
    assert bundle.classification.document_type is DocumentType.AUDITOR_REPORT
    assert bundle.classification.lifecycle_status is DocumentLifecycleStatus.FINAL


def test_auditor_draft_classification() -> None:
    bundle = _classify(
        "Turan Verity Audit LLP\nПРОЕКТ — ПРОМЕЖУТОЧНАЯ ВЕДОМОСТЬ\n"
        "DRAFT interim worksheet for agreed-upon procedures discussion"
    )
    assert bundle.classification.document_type is DocumentType.AGREED_UPON_PROCEDURES_REPORT
    assert bundle.classification.lifecycle_status in {
        DocumentLifecycleStatus.DRAFT,
        DocumentLifecycleStatus.PRELIMINARY,
        DocumentLifecycleStatus.SUPERSEDED,
    }


def test_audit_planning_memo_not_final_audit() -> None:
    bundle = _classify(
        "Финансы — Аудит 2024 — Рабочий проект\nВнешний аудит — Записка о планировании\n"
        "audit planning memorandum for internal use"
    )
    assert bundle.classification.document_type is DocumentType.BOARD_OR_INTERNAL_MEMO
    assert AuthorityDomain.FINANCIAL_ADJUSTMENTS not in bundle.classification.authority_domains


def test_kyc_dossier_classification() -> None:
    bundle = _classify(
        "Управление финансового мониторинга и комплаенса\n"
        "Клиентское досье\nBeneficial owners / бенефициарные владельцы\n"
        "структура собственности"
    )
    assert bundle.classification.document_type is DocumentType.KYC_DOSSIER
    assert AuthorityDomain.KYC_RELATIONSHIPS in bundle.classification.authority_domains


def test_kyc_policy_not_dossier() -> None:
    bundle = _classify(
        "KYC policy and procedure\nОбщая процедура KYC\n"
        "периодическое обновление данных клиента\nworkflow instructions"
    )
    assert bundle.classification.document_type is DocumentType.KYC_POLICY_OR_PROCEDURE
    assert bundle.classification.authority_domains == (AuthorityDomain.NONE,)


def test_group_report_classification() -> None:
    bundle = _classify(
        "CONSOLIDATED ANNUAL REPORT\nConsolidated Financial Statements\n"
        "Note 6 — Segment Information\nThe Group's generation segment",
        group=True,
    )
    assert bundle.classification.document_type is DocumentType.GROUP_OR_CONSOLIDATED_REPORT
    assert AuthorityDomain.GROUP_STRUCTURE in bundle.classification.authority_domains


def test_press_release_not_group_report() -> None:
    bundle = _classify(
        "Пресс-релиз — ЧЕРНОВИК\nКомпания сегодня объявила о ряде инициатив\npress release embargo"
    )
    assert bundle.classification.document_type is DocumentType.PRESS_RELEASE
    assert AuthorityDomain.GROUP_STRUCTURE not in bundle.classification.authority_domains
    assert AuthorityDomain.NONE in bundle.classification.authority_domains


def test_treasury_memo_classification() -> None:
    bundle = _classify(
        "Операционное управление казначейства\nКАЗНАЧЕЙСТВО ГРУППЫ — РЕЗЕРВЫ\nTreasury Memo"
    )
    assert bundle.classification.document_type is DocumentType.TREASURY_MEMO
    assert AuthorityDomain.TREASURY_FACTS in bundle.classification.authority_domains


def test_unknown_safe_fallback() -> None:
    bundle = _classify("Cafeteria menu and parking policy. No bank identifiers.")
    assert bundle.classification.document_type is DocumentType.UNKNOWN
    assert bundle.classification.lifecycle_status is DocumentLifecycleStatus.UNKNOWN
