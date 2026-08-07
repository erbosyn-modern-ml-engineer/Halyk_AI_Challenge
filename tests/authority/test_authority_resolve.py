"""Lifecycle and authority resolution tests (Stage 5C)."""

# ruff: noqa: RUF001

from __future__ import annotations

from halyk_agent.domain.authority.engine import run_authority
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


def test_explicit_superseded_loan_lifecycle() -> None:
    doc = make_document(
        artifact="obs",
        raw_text=(
            "НЕДЕЙСТВУЮЩАЯ РЕДАКЦИЯ (2024 г.). Заменена и изложена в новой редакции "
            "действующим Договором текущего периода. НЕ ПРИМЕНЯЕТСЯ.\n"
            "ДОГОВОР БАНКОВСКОГО ЗАЙМА № ACC-7801"
        ),
    )
    report = run_authority(
        documents=(doc,),
        document_links=(make_link(doc, scenario_ids=("P1",)),),
        routing_manifest=_manifest(),
    )
    item = report.classifications[0]
    assert item.document_type is DocumentType.LOAN_AGREEMENT
    assert item.lifecycle_status is DocumentLifecycleStatus.SUPERSEDED


def test_current_agreement_outranks_obsolete() -> None:
    current = make_document(
        artifact="cur",
        sha="b" * 64,
        raw_text=(
            "ИСПОЛНИТЕЛЬНЫЙ ЭКЗЕМПЛЯР\nДОГОВОР БАНКОВСКОГО ЗАЙМА № ACC-7801\nот 1 января 2025 года"
        ),
    )
    obsolete = make_document(
        artifact="obs",
        sha="c" * 64,
        raw_text=(
            "НЕДЕЙСТВУЮЩАЯ РЕДАКЦИЯ. Заменена. НЕ ПРИМЕНЯЕТСЯ.\n"
            "ДОГОВОР БАНКОВСКОГО ЗАЙМА № ACC-7801"
        ),
    )
    report = run_authority(
        documents=(obsolete, current),
        document_links=(
            make_link(obsolete, scenario_ids=("P1",)),
            make_link(current, scenario_ids=("P1",)),
        ),
        routing_manifest=_manifest(),
    )
    decision = next(
        d
        for d in report.decisions
        if d.domain is AuthorityDomain.COVENANT_TERMS and d.scenario_id == "P1"
    )
    assert decision.status is AuthorityStatus.AUTHORITATIVE
    assert decision.winning_document_ids == (current.document_id,)
    assert obsolete.document_id in decision.rejected_document_ids


def test_final_audit_outranks_draft() -> None:
    final = make_document(
        artifact="fin",
        sha="d" * 64,
        raw_text=(
            "Turan Verity Audit LLP\nНезависимый аудитор\nАУДИТОРСКОЕ ДЕЛО №ACC-7801\n"
            "окончательный отчет"
        ),
    )
    draft = make_document(
        artifact="dr",
        sha="e" * 64,
        raw_text=(
            "Turan Verity Audit LLP\nПРОЕКТ — ПРОМЕЖУТОЧНАЯ ВЕДОМОСТЬ\n"
            "согласованных процедур. DRAFT"
        ),
    )
    report = run_authority(
        documents=(draft, final),
        document_links=(
            make_link(draft, scenario_ids=("P1",)),
            make_link(final, scenario_ids=("P1",)),
        ),
        routing_manifest=_manifest(),
    )
    decision = next(
        d for d in report.decisions if d.domain is AuthorityDomain.FINANCIAL_ADJUSTMENTS
    )
    assert decision.status is AuthorityStatus.AUTHORITATIVE
    assert decision.winning_document_ids == (final.document_id,)
    assert draft.document_id in decision.rejected_document_ids


def test_equal_authoritative_candidates_conflict() -> None:
    a = make_document(
        artifact="a1",
        sha="1" * 64,
        raw_text="ИСПОЛНИТЕЛЬНЫЙ ЭКЗЕМПЛЯР\nДОГОВОР БАНКОВСКОГО ЗАЙМА № ACC-7801",
    )
    b = make_document(
        artifact="a2",
        sha="2" * 64,
        raw_text="ИСПОЛНИТЕЛЬНЫЙ ЭКЗЕМПЛЯР\nДОГОВОР БАНКОВСКОГО ЗАЙМА № ACC-7999",
    )
    report = run_authority(
        documents=(a, b),
        document_links=(
            make_link(a, scenario_ids=("P1",)),
            make_link(b, scenario_ids=("P1",)),
        ),
        routing_manifest=_manifest(),
    )
    decision = next(d for d in report.decisions if d.domain is AuthorityDomain.COVENANT_TERMS)
    assert decision.status is AuthorityStatus.UNRESOLVED
    assert report.conflicts


def test_missing_domain_emits_missing_authority() -> None:
    doc = make_document(raw_text="Пресс-релиз — новости компании")
    report = run_authority(
        documents=(doc,),
        document_links=(make_link(doc, scenario_ids=("P1",)),),
        routing_manifest=_manifest(),
    )
    covenant = next(d for d in report.decisions if d.domain is AuthorityDomain.COVENANT_TERMS)
    assert covenant.status is AuthorityStatus.MISSING_AUTHORITY


def test_kyc_policy_never_substitutes_for_dossier() -> None:
    policy = make_document(
        artifact="pol",
        raw_text="KYC policy and procedure\nОбщая процедура KYC\nпериодическое обновление",
    )
    report = run_authority(
        documents=(policy,),
        document_links=(make_link(policy, scenario_ids=("P1",)),),
        routing_manifest=_manifest(),
    )
    kyc = next(d for d in report.decisions if d.domain is AuthorityDomain.KYC_RELATIONSHIPS)
    assert kyc.status is AuthorityStatus.MISSING_AUTHORITY
    assert policy.document_id not in kyc.winning_document_ids


def test_no_weak_date_override_of_explicit_superseded() -> None:
    obsolete = make_document(
        artifact="old",
        raw_text=(
            "НЕДЕЙСТВУЮЩАЯ РЕДАКЦИЯ. НЕ ПРИМЕНЯЕТСЯ.\n"
            "ДОГОВОР БАНКОВСКОГО ЗАЙМА № ACC-7801\nот 1 января 2026 года"
        ),
    )
    current = make_document(
        artifact="new",
        sha="f" * 64,
        raw_text=(
            "ИСПОЛНИТЕЛЬНЫЙ ЭКЗЕМПЛЯР\nДОГОВОР БАНКОВСКОГО ЗАЙМА № ACC-7801\nот 1 января 2024 года"
        ),
    )
    report = run_authority(
        documents=(obsolete, current),
        document_links=(
            make_link(obsolete, scenario_ids=("P1",)),
            make_link(current, scenario_ids=("P1",)),
        ),
        routing_manifest=_manifest(),
    )
    decision = next(d for d in report.decisions if d.domain is AuthorityDomain.COVENANT_TERMS)
    assert decision.winning_document_ids == (current.document_id,)
    assert obsolete.document_id in decision.rejected_document_ids
