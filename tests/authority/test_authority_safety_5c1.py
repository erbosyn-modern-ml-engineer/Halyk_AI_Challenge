"""Stage 5C.1 targeted authority safety regressions."""

# ruff: noqa: RUF001

from __future__ import annotations

import pytest

from halyk_agent.domain.authority.classify import classify_document
from halyk_agent.domain.authority.engine import run_authority
from halyk_agent.domain.authority.metadata import extract_metadata
from halyk_agent.domain.authority.models import (
    AuthorityDomain,
    AuthorityStatus,
    DocumentLifecycleStatus,
    DocumentType,
)
from halyk_agent.domain.routing.models import RoutingManifest
from tests.authority.helpers import make_document, make_link


def _manifest() -> RoutingManifest:
    return RoutingManifest(
        dataset_manifest_hash="d" * 64,
        canonical_documents_hash="c" * 64,
        scenario_count=1,
        resolved_document_count=1,
        unresolved_document_count=0,
        transaction_link_count=0,
        conflict_count=0,
        template_cell_count=1,
        ledger_row_count=0,
        scenario_transaction_count=0,
        multi_scenario_document_count=0,
    )


def _classify(text: str):
    doc = make_document(raw_text=text)
    meta = extract_metadata(doc)
    return classify_document(doc, metadata=meta, link=make_link(doc))


@pytest.mark.parametrize(
    "text",
    [
        "ПРОЕКТ — КРЕДИТНОЕ СОГЛАШЕНИЕ\nДата вступления в силу: 01.01.2025\n"
        "ДОГОВОР БАНКОВСКОГО ЗАЙМА № ACC-7801",
        "DRAFT FACILITY AGREEMENT\nEffective Date: 1 January 2025\n"
        "ДОГОВОР БАНКОВСКОГО ЗАЙМА № ACC-7801",
    ],
)
def test_loan_draft_not_overridden_by_effective_date(text: str) -> None:
    bundle = _classify(text)
    assert bundle.classification.document_type is DocumentType.LOAN_AGREEMENT
    assert bundle.classification.lifecycle_status is DocumentLifecycleStatus.DRAFT
    assert bundle.classification.lifecycle_status is not DocumentLifecycleStatus.CURRENT_EXECUTED


def test_loan_strong_execution_marker() -> None:
    bundle = _classify(
        "ИСПОЛНИТЕЛЬНЫЙ ЭКЗЕМПЛЯР\nДОГОВОР БАНКОВСКОГО ЗАЙМА № ACC-7801\n"
        "Effective Date: 1 January 2025"
    )
    assert bundle.classification.lifecycle_status is DocumentLifecycleStatus.CURRENT_EXECUTED


def test_loan_superseded_outranks_effective_date() -> None:
    bundle = _classify(
        "НЕДЕЙСТВУЮЩАЯ РЕДАКЦИЯ. НЕ ПРИМЕНЯЕТСЯ.\n"
        "ДОГОВОР БАНКОВСКОГО ЗАЙМА № ACC-7801\n"
        "Дата вступления в силу: 01.01.2026"
    )
    assert bundle.classification.lifecycle_status is DocumentLifecycleStatus.SUPERSEDED


@pytest.mark.parametrize(
    "text",
    [
        "ПРОЕКТ — ПРОМЕЖУТОЧНАЯ ВЕДОМОСТЬ\n"
        "Настоящий документ НЕ ЯВЛЯЕТСЯ ОКОНЧАТЕЛЬНОЙ ПОЗИЦИЕЙ аудитора.\n"
        "согласованных процедур",
        "DRAFT AUDITOR MEMO\nThis is not the final auditor position.\n"
        "АУДИТОРСКОЕ ДЕЛО №ACC-7801\nНезависимый аудитор",
    ],
)
def test_auditor_draft_negated_final_stays_non_final(text: str) -> None:
    bundle = _classify(text)
    assert bundle.classification.lifecycle_status in {
        DocumentLifecycleStatus.DRAFT,
        DocumentLifecycleStatus.PRELIMINARY,
        DocumentLifecycleStatus.WORKING_PAPER,
    }
    assert bundle.classification.lifecycle_status is not DocumentLifecycleStatus.FINAL


def test_auditor_true_final_header() -> None:
    bundle = _classify("FINAL AUDITOR'S REPORT\nНезависимый аудитор\nАУДИТОРСКОЕ ДЕЛО №ACC-7801")
    assert bundle.classification.document_type is DocumentType.AUDITOR_REPORT
    assert bundle.classification.lifecycle_status is DocumentLifecycleStatus.FINAL


def test_auditor_true_final_aup_position() -> None:
    bundle = _classify(
        "Отчёт о выполнении согласованных процедур проверки\n"
        "ЭКЗЕМПЛЯР АУДИТОРА\n"
        "Приведённые ниже выводы являются окончательной позицией аудитора "
        "для целей проверки ковенантов."
    )
    assert bundle.classification.document_type is DocumentType.AGREED_UPON_PROCEDURES_REPORT
    assert bundle.classification.lifecycle_status is DocumentLifecycleStatus.FINAL


def test_treasury_working_doc_not_final_from_approved_noun() -> None:
    text = (
        "Операционное управление казначейства\nКАЗНАЧЕЙСТВО ГРУППЫ — РЕЗЕРВЫ\n"
        "Treasury Memo\nРАБОЧИЙ ДОКУМЕНТ\nДЛЯ ВНУТРЕННЕГО ПОЛЬЗОВАНИЯ\n"
        "Депозитные и расчётные лимиты сверены с утверждённым перечнем контрагентов."
    )
    doc = make_document(raw_text=text, artifact="tre", sha="t" * 64)
    report = run_authority(
        documents=(doc,),
        document_links=(make_link(doc, scenario_ids=("P7",)),),
        routing_manifest=_manifest(),
    )
    item = report.classifications[0]
    assert item.document_type is DocumentType.TREASURY_MEMO
    assert item.lifecycle_status is DocumentLifecycleStatus.WORKING_PAPER
    assert item.lifecycle_status is not DocumentLifecycleStatus.FINAL
    decision = next(d for d in report.decisions if d.domain is AuthorityDomain.TREASURY_FACTS)
    assert decision.status is AuthorityStatus.AUTHORITATIVE
    assert decision.winning_document_ids == (doc.document_id,)
    assert decision.rule_id == "RULE_TREASURY_MEMO_WORKING_AUTHORITY"


def test_generic_internal_memo_not_treasury_authority() -> None:
    text = (
        "Internal operations memo\nРАБОЧИЙ ДОКУМЕНТ\n"
        "Please use the approved vendor list for purchases."
    )
    doc = make_document(raw_text=text, artifact="gen", sha="g" * 64)
    report = run_authority(
        documents=(doc,),
        document_links=(make_link(doc, scenario_ids=("P7",)),),
        routing_manifest=_manifest(),
    )
    item = report.classifications[0]
    assert item.document_type is not DocumentType.TREASURY_MEMO
    assert item.lifecycle_status is not DocumentLifecycleStatus.FINAL
    assert AuthorityDomain.TREASURY_FACTS not in item.authority_domains
    assert not any(
        d.domain is AuthorityDomain.TREASURY_FACTS and d.status is AuthorityStatus.AUTHORITATIVE
        for d in report.decisions
    )


def test_persisted_evidence_refs_resolvable() -> None:
    doc = make_document(
        raw_text=(
            "ИСПОЛНИТЕЛЬНЫЙ ЭКЗЕМПЛЯР\nДОГОВОР БАНКОВСКОГО ЗАЙМА № ACC-7801\nот 1 января 2025 года"
        )
    )
    report = run_authority(
        documents=(doc,),
        document_links=(make_link(doc),),
        routing_manifest=_manifest(),
    )
    evidence_ids = {e.evidence_span_id for e in report.evidence}
    referenced: set[str] = set()
    for meta in report.metadata:
        referenced.update(meta.evidence_span_ids)
    for item in report.classifications:
        referenced.update(item.evidence_span_ids)
    for decision in report.decisions:
        referenced.update(decision.evidence_span_ids)
    assert referenced - evidence_ids == set()


def test_superseded_aup_in_rejected_trace() -> None:
    final = make_document(
        artifact="fin",
        sha="d" * 64,
        raw_text=(
            "Turan Verity Audit LLP\nНезависимый аудитор\nАУДИТОРСКОЕ ДЕЛО №ACC-7801\n"
            "окончательный аудиторский отчет"
        ),
    )
    superseded = make_document(
        artifact="sup",
        sha="e" * 64,
        raw_text=(
            "НЕДЕЙСТВУЮЩАЯ РЕДАКЦИЯ. Заменена окончательным отчётом.\n"
            "Отчёт о выполнении согласованных процедур\nAUP DRAFT"
        ),
    )
    report = run_authority(
        documents=(final, superseded),
        document_links=(make_link(final), make_link(superseded)),
        routing_manifest=_manifest(),
    )
    decision = next(
        d for d in report.decisions if d.domain is AuthorityDomain.FINANCIAL_ADJUSTMENTS
    )
    assert decision.status is AuthorityStatus.AUTHORITATIVE
    assert decision.winning_document_ids == (final.document_id,)
    assert superseded.document_id in decision.rejected_document_ids
    assert superseded.document_id in decision.candidate_document_ids
