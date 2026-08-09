"""Deterministic document type and lifecycle classification (Stage 5C)."""

# ruff: noqa: RUF001

from __future__ import annotations

from dataclasses import dataclass

from halyk_agent.domain.authority.evidence import (
    find_first_span_non_negated,
    require_span_or_none,
)
from halyk_agent.domain.authority.models import (
    AuthorityDomain,
    ClassificationConfidence,
    DocumentClassification,
    DocumentLifecycleStatus,
    DocumentMetadata,
    DocumentType,
)
from halyk_agent.domain.evidence import EvidenceSpan
from halyk_agent.domain.parsing import CanonicalDocument
from halyk_agent.domain.routing.models import DocumentEntityLink


@dataclass(frozen=True, slots=True)
class ClassificationBundle:
    classification: DocumentClassification
    spans: tuple[EvidenceSpan, ...]


def _head_blob(document: CanonicalDocument, *, chars: int = 4000) -> str:
    parts: list[str] = []
    remaining = chars
    for page in document.pages:
        text = page.raw_text or ""
        if not text:
            continue
        parts.append(text[:remaining])
        remaining -= len(parts[-1])
        if remaining <= 0:
            break
    return "\n".join(parts)


def _lifecycle_from_markers(
    document: CanonicalDocument,
    *,
    doc_type: DocumentType,
    metadata: DocumentMetadata,
) -> tuple[DocumentLifecycleStatus, EvidenceSpan | None, str]:
    """
    Resolve lifecycle using explicit signal strength.

    SUPERSEDED/OBSOLETE and DRAFT/PROJECT outrank weak field phrases such as
    Effective Date or incidental body words like "final"/"утверждённый".
    Strong execution / strong final status markers are required to establish
    CURRENT_EXECUTED / FINAL.
    """
    superseded = require_span_or_none(
        document,
        patterns=(
            "НЕДЕЙСТВУЮЩАЯ РЕДАКЦИЯ",
            "НЕ ПРИМЕНЯЕТСЯ",
            "superseded",
            "SUPERSEDED",
            "obsolete",
            "OBSOLETE",
            "заменена окончательным",
            "заменена и изложена",
        ),
    )
    if superseded is not None:
        return DocumentLifecycleStatus.SUPERSEDED, superseded, "EXPLICIT_SUPERSEDED"

    if metadata.superseded_marker:
        span = require_span_or_none(document, patterns=(metadata.superseded_marker,))
        if span is not None:
            return DocumentLifecycleStatus.SUPERSEDED, span, "EXPLICIT_SUPERSEDED"

    working_doc = require_span_or_none(
        document,
        patterns=(
            "РАБОЧИЙ ДОКУМЕНТ",
            "Рабочий документ",
            "рабочий документ",
            "внутренний рабочий документ",
            "ДЛЯ ВНУТРЕННЕГО ПОЛЬЗОВАНИЯ",
            "WORKING DOCUMENT",
            "Working Document",
            "working document",
        ),
    )

    draft = require_span_or_none(
        document,
        patterns=(
            "ПРОЕКТ — ПРОМЕЖУТОЧНАЯ",
            "ПРОЕКТ —",
            "ПРОЕКТ",
            "черновик",
            "ЧЕРНОВИК",
            "DRAFT",
            "Draft",
            "draft",
            "preliminary report",
            "Preliminary Report",
            "предварительный отчет",
            "предварительный отчёт",
            "предварительная ведомость",
            "рабочий проект",
            "working paper",
            "WORKING PAPER",
            "interim worksheet",
        ),
    )

    # Strong execution-status markers only (not Effective Date field/clause).
    strong_executed = require_span_or_none(
        document,
        patterns=(
            "ИСПОЛНИТЕЛЬНЫЙ ЭКЗЕМПЛЯР",
            "ДЕЙСТВУЮЩАЯ РЕДАКЦИЯ",
            "EXECUTED COPY",
            "Executed Copy",
            "SIGNED AND EXECUTED",
            "Signed and Executed",
            "CURRENT_EXECUTED",
        ),
    )

    # Strong final-status markers; reject negated local contexts.
    # Do NOT use unbounded body words final/окончательн*/итогов*/утверждённ*.
    strong_final = find_first_span_non_negated(
        document,
        patterns=(
            "FINAL AUDITOR'S REPORT",
            "Final Auditor's Report",
            "FINAL AUDITOR REPORT",
            "FINAL REPORT",
            "Final Report",
            "окончательный аудиторский отчет",
            "окончательный аудиторский отчёт",
            "окончательный отчет",
            "окончательный отчёт",
            "итоговый аудиторский отчет",
            "итоговый аудиторский отчёт",
            "окончательной позицией аудитора",
            "окончательная позиция аудитора",
            "являются окончательной позицией",
            "является окончательной позицией",
            "FINAL AUP",
            "Final AUP Report",
            "ЭКЗЕМПЛЯР АУДИТОРА",
        ),
    )

    if doc_type is DocumentType.LOAN_AGREEMENT:
        # Explicit DRAFT/PROJECT must not be overridden by Effective Date clauses.
        if draft is not None:
            return DocumentLifecycleStatus.DRAFT, draft, "EXPLICIT_DRAFT"
        if strong_executed is not None:
            return (
                DocumentLifecycleStatus.CURRENT_EXECUTED,
                strong_executed,
                "STRONG_EXECUTION_STATUS",
            )
        # Weak effective-date fields are metadata only — not CURRENT_EXECUTED.
        return DocumentLifecycleStatus.UNKNOWN, None, "NO_LIFECYCLE_SIGNAL"

    if doc_type in {
        DocumentType.AUDITOR_REPORT,
        DocumentType.AGREED_UPON_PROCEDURES_REPORT,
    }:
        # Explicit draft/preliminary outranks weak body "final" mentions.
        if draft is not None:
            low = (draft.quote or "").casefold()
            if "working" in low or "рабоч" in low:
                return DocumentLifecycleStatus.WORKING_PAPER, draft, "EXPLICIT_WORKING_PAPER"
            if "prelimin" in low or "промежуточ" in low or "предварит" in low:
                return DocumentLifecycleStatus.PRELIMINARY, draft, "EXPLICIT_PRELIMINARY"
            return DocumentLifecycleStatus.DRAFT, draft, "EXPLICIT_DRAFT"
        if strong_final is not None:
            return DocumentLifecycleStatus.FINAL, strong_final, "STRONG_FINAL_STATUS"
        return DocumentLifecycleStatus.UNKNOWN, None, "NO_LIFECYCLE_SIGNAL"

    if doc_type is DocumentType.TREASURY_MEMO:
        if working_doc is not None:
            return DocumentLifecycleStatus.WORKING_PAPER, working_doc, "EXPLICIT_WORKING_DOCUMENT"
        if draft is not None:
            return DocumentLifecycleStatus.DRAFT, draft, "EXPLICIT_DRAFT"
        # Do not infer FINAL from unrelated "утверждённый" body nouns.
        if strong_final is not None:
            return DocumentLifecycleStatus.FINAL, strong_final, "STRONG_FINAL_STATUS"
        return DocumentLifecycleStatus.UNKNOWN, None, "NO_LIFECYCLE_SIGNAL"

    if doc_type is DocumentType.KYC_DOSSIER:
        if draft is not None:
            return DocumentLifecycleStatus.DRAFT, draft, "EXPLICIT_DRAFT"
        if strong_executed is not None or strong_final is not None:
            return (
                DocumentLifecycleStatus.CURRENT,
                strong_executed or strong_final,
                "KYC_CURRENT",
            )
        return DocumentLifecycleStatus.UNKNOWN, None, "NO_LIFECYCLE_SIGNAL"

    if working_doc is not None:
        return DocumentLifecycleStatus.WORKING_PAPER, working_doc, "EXPLICIT_WORKING_DOCUMENT"
    if draft is not None:
        return DocumentLifecycleStatus.DRAFT, draft, "EXPLICIT_DRAFT"
    if strong_final is not None:
        return DocumentLifecycleStatus.FINAL, strong_final, "STRONG_FINAL_STATUS"
    return DocumentLifecycleStatus.UNKNOWN, None, "NO_LIFECYCLE_SIGNAL"


def _domains_for(
    doc_type: DocumentType,
    lifecycle: DocumentLifecycleStatus,
) -> tuple[AuthorityDomain, ...]:
    # Treasury working memos may still establish TREASURY_FACTS (type ≠ lifecycle).
    # DRAFT is not granted here — only WORKING_PAPER / UNKNOWN keep domain candidacy.
    if doc_type is DocumentType.TREASURY_MEMO and lifecycle in {
        DocumentLifecycleStatus.WORKING_PAPER,
        DocumentLifecycleStatus.UNKNOWN,
    }:
        return (AuthorityDomain.TREASURY_FACTS,)

    if lifecycle in {
        DocumentLifecycleStatus.SUPERSEDED,
        DocumentLifecycleStatus.OBSOLETE,
        DocumentLifecycleStatus.EXPIRED,
        DocumentLifecycleStatus.DRAFT,
        DocumentLifecycleStatus.PRELIMINARY,
        DocumentLifecycleStatus.WORKING_PAPER,
    }:
        # Draft/superseded docs may still be typed but do not grant authority domains
        # except as rejected candidates (resolver handles rejection).
        if doc_type is DocumentType.LOAN_AGREEMENT:
            return (AuthorityDomain.COVENANT_TERMS,)
        if doc_type in {
            DocumentType.AUDITOR_REPORT,
            DocumentType.AGREED_UPON_PROCEDURES_REPORT,
        }:
            return (AuthorityDomain.FINANCIAL_ADJUSTMENTS,)
        if doc_type is DocumentType.KYC_DOSSIER:
            return (AuthorityDomain.KYC_RELATIONSHIPS,)
        return (AuthorityDomain.NONE,)

    mapping: dict[DocumentType, tuple[AuthorityDomain, ...]] = {
        DocumentType.LOAN_AGREEMENT: (AuthorityDomain.COVENANT_TERMS,),
        DocumentType.AUDITOR_REPORT: (AuthorityDomain.FINANCIAL_ADJUSTMENTS,),
        DocumentType.AGREED_UPON_PROCEDURES_REPORT: (AuthorityDomain.FINANCIAL_ADJUSTMENTS,),
        DocumentType.KYC_DOSSIER: (AuthorityDomain.KYC_RELATIONSHIPS,),
        DocumentType.KYC_POLICY_OR_PROCEDURE: (AuthorityDomain.NONE,),
        DocumentType.GROUP_OR_CONSOLIDATED_REPORT: (AuthorityDomain.GROUP_STRUCTURE,),
        DocumentType.TREASURY_MEMO: (AuthorityDomain.TREASURY_FACTS,),
        DocumentType.PRESS_RELEASE: (AuthorityDomain.NONE,),
        DocumentType.IT_OR_OPERATIONS_DOCUMENT: (AuthorityDomain.NONE,),
        DocumentType.HR_OR_ADMIN_DOCUMENT: (AuthorityDomain.NONE,),
        DocumentType.BOARD_OR_INTERNAL_MEMO: (AuthorityDomain.GENERAL_CONTEXT,),
        DocumentType.CORPORATE_REPORT: (AuthorityDomain.GENERAL_CONTEXT,),
        DocumentType.OTHER_BUSINESS_DOCUMENT: (AuthorityDomain.GENERAL_CONTEXT,),
        DocumentType.UNKNOWN: (AuthorityDomain.NONE,),
    }
    return mapping.get(doc_type, (AuthorityDomain.NONE,))


def _classify_type(
    document: CanonicalDocument,
    *,
    link: DocumentEntityLink | None,
    head: str,
) -> tuple[DocumentType, EvidenceSpan | None, str, ClassificationConfidence]:
    low = head.casefold()

    # Strong obsolete/current loan agreement signals first.
    loan_span = require_span_or_none(
        document,
        patterns=(
            "ДОГОВОР БАНКОВСКОГО ЗАЙМА",
            "Договор банковского займа",
            "Loan Agreement",
            "Facility Agreement",
            "Credit Agreement",
            "Кредитное соглашение",
            "Кредитный договор",
            "Договор займа",
            "Қарыз шарты",
            "Кредиттік келісім",
        ),
    )
    if loan_span is not None:
        return (
            DocumentType.LOAN_AGREEMENT,
            loan_span,
            "RULE_LOAN_AGREEMENT_TITLE",
            ClassificationConfidence.DECLARED,
        )

    # Group / consolidated report (independent of routing marker).
    group_span = require_span_or_none(
        document,
        patterns=(
            "CONSOLIDATED ANNUAL REPORT",
            "Consolidated Financial Statements",
            "Segment Information",
            "консолидированн",
            "Консолидированная финансовая отчетность",
        ),
    )
    if group_span is not None and (
        "segment" in low
        or "consolidat" in low
        or "консолидир" in low
        or (link is not None and link.group_document)
    ):
        return (
            DocumentType.GROUP_OR_CONSOLIDATED_REPORT,
            group_span,
            "RULE_GROUP_CONSOLIDATED_REPORT",
            ClassificationConfidence.DECLARED,
        )

    # Audit planning memo — NOT final auditor evidence.
    planning = require_span_or_none(
        document,
        patterns=(
            "Записка о планировании",
            "записка о планировании",
            "planning memorandum",
            "Audit Planning",
            "аудит — записка",
        ),
    )
    if planning is not None and ("аудит" in low or "audit" in low):
        return (
            DocumentType.BOARD_OR_INTERNAL_MEMO,
            planning,
            "RULE_AUDIT_PLANNING_MEMO",
            ClassificationConfidence.DECLARED,
        )

    # Agreed-upon procedures (before generic auditor).
    aup = require_span_or_none(
        document,
        patterns=(
            "согласованных процедур",
            "согласованные процедуры",
            "Agreed-Upon Procedures",
            "agreed-upon procedures",
            "AUP",
        ),
    )
    if aup is not None and (
        "аудитор" in low or "audit" in low or "procedure" in low or "процедур" in low
    ):
        return (
            DocumentType.AGREED_UPON_PROCEDURES_REPORT,
            aup,
            "RULE_AUP_REPORT",
            ClassificationConfidence.DECLARED,
        )

    auditor = require_span_or_none(
        document,
        patterns=(
            "АУДИТОРСКОЕ ДЕЛО",
            "Независимый аудитор",
            "Independent Auditor",
            "independent auditor's report",
            "Independent Auditor's Report",
            "Auditor's Report",
            "аудиторский отчёт",
            "аудиторский отчет",
        ),
    )
    if auditor is not None:
        return (
            DocumentType.AUDITOR_REPORT,
            auditor,
            "RULE_AUDITOR_REPORT",
            ClassificationConfidence.DECLARED,
        )

    # KYC dossier vs policy
    kyc_dossier = require_span_or_none(
        document,
        patterns=(
            "бенефициарн",
            "beneficial owner",
            "Beneficial Owner",
            "KYC-досье",
            "KYC dossier",
            "досье клиента",
            "клиентское досье",
            "структура собственности",
            "ownership structure",
            "финансового мониторинга и комплаенса",
        ),
    )
    kyc_policy = require_span_or_none(
        document,
        patterns=(
            "KYC-политик",
            "KYC policy",
            "политика KYC",
            "процедура KYC",
            "KYC procedure",
            "порядок проведения KYC",
            "периодическое обновление",
        ),
    )
    if (
        kyc_dossier is not None
        and kyc_policy is None
        and any(
            token in low
            for token in (
                "бенефициар",
                "beneficial",
                "досье",
                "собственн",
                "ownership",
                "комплаенса",
                "мониторинга",
            )
        )
    ):
        return (
            DocumentType.KYC_DOSSIER,
            kyc_dossier,
            "RULE_KYC_DOSSIER",
            ClassificationConfidence.DECLARED,
        )
    if kyc_policy is not None or (
        "kyc" in low
        and any(t in low for t in ("процедур", "политик", "procedure", "policy", "workflow"))
    ):
        span = kyc_policy or require_span_or_none(document, patterns=("KYC", "kyc"))
        if span is not None:
            return (
                DocumentType.KYC_POLICY_OR_PROCEDURE,
                span,
                "RULE_KYC_POLICY",
                ClassificationConfidence.DECLARED,
            )

    treasury = require_span_or_none(
        document,
        patterns=(
            "казначейств",
            "КАЗНАЧЕЙСТВО",
            "Treasury Memo",
            "treasury memo",
            "Treasury",
        ),
    )
    if treasury is not None and any(t in low for t in ("казначей", "treasury")):
        return (
            DocumentType.TREASURY_MEMO,
            treasury,
            "RULE_TREASURY_MEMO",
            ClassificationConfidence.DECLARED,
        )

    press = require_span_or_none(
        document,
        patterns=("Пресс-релиз", "пресс-релиз", "Press Release", "PRESS RELEASE"),
    )
    if press is not None:
        return (
            DocumentType.PRESS_RELEASE,
            press,
            "RULE_PRESS_RELEASE",
            ClassificationConfidence.DECLARED,
        )

    it_ops = require_span_or_none(
        document,
        patterns=(
            "ИТ-руководство",
            "IT-руководство",
            "операционное руководство",
            "Плановое обслуживание",
            "IT operations",
            "operations guide",
        ),
    )
    if it_ops is not None:
        return (
            DocumentType.IT_OR_OPERATIONS_DOCUMENT,
            it_ops,
            "RULE_IT_OPS",
            ClassificationConfidence.DECLARED,
        )

    hr = require_span_or_none(
        document,
        patterns=(
            "кадров",
            "HR policy",
            "административно-хозяйственн",
            "персонал",
            "employee handbook",
        ),
    )
    if hr is not None:
        return (
            DocumentType.HR_OR_ADMIN_DOCUMENT,
            hr,
            "RULE_HR_ADMIN",
            ClassificationConfidence.DECLARED,
        )

    brand = require_span_or_none(
        document,
        patterns=("Руководство по бренду", "brand guide", "Brand Guide", "логотип"),
    )
    if brand is not None:
        return (
            DocumentType.OTHER_BUSINESS_DOCUMENT,
            brand,
            "RULE_BRAND_OR_CORP_NOISE",
            ClassificationConfidence.DECLARED,
        )

    board = require_span_or_none(
        document,
        patterns=(
            "меморандум",
            "Memorandum",
            "совет директоров",
            "Board Memo",
            "внутренн",
            "общее собрание",
        ),
    )
    if board is not None and ("memo" in low or "меморандум" in low or "собрание" in low):
        return (
            DocumentType.BOARD_OR_INTERNAL_MEMO,
            board,
            "RULE_BOARD_INTERNAL_MEMO",
            ClassificationConfidence.DERIVED,
        )

    corporate = require_span_or_none(
        document,
        patterns=("годовой отчёт", "годовой отчет", "Annual Report", "корпоративн"),
    )
    if corporate is not None:
        return (
            DocumentType.CORPORATE_REPORT,
            corporate,
            "RULE_CORPORATE_REPORT",
            ClassificationConfidence.DERIVED,
        )

    return DocumentType.UNKNOWN, None, "RULE_UNKNOWN_SAFE", ClassificationConfidence.UNRESOLVED


def classify_document(
    document: CanonicalDocument,
    *,
    metadata: DocumentMetadata,
    link: DocumentEntityLink | None,
) -> ClassificationBundle:
    """Classify one document with evidence-backed type/lifecycle/domains."""
    head = _head_blob(document)
    doc_type, type_span, type_rule, confidence = _classify_type(document, link=link, head=head)
    lifecycle, life_span, life_reason = _lifecycle_from_markers(
        document, doc_type=doc_type, metadata=metadata
    )
    domains = _domains_for(doc_type, lifecycle)

    spans: list[EvidenceSpan] = []
    span_ids: list[str] = []
    for span in (type_span, life_span):
        if span is None:
            continue
        spans.append(span)
        span_ids.append(span.id)
    for span_id in metadata.evidence_span_ids:
        if span_id not in span_ids:
            span_ids.append(span_id)

    if doc_type is DocumentType.UNKNOWN and type_span is None:
        confidence = ClassificationConfidence.UNRESOLVED

    scenario_ids = link.scenario_ids if link is not None else ()
    classification = DocumentClassification(
        document_id=document.document_id,
        document_version_id=document.document_version_id,
        scenario_ids=scenario_ids,
        document_type=doc_type,
        lifecycle_status=lifecycle,
        authority_domains=domains,
        confidence=confidence,
        rule_id=type_rule,
        reason_code=life_reason,
        evidence_span_ids=tuple(span_ids),
        group_document_routing=bool(link.group_document) if link else False,
    )
    return ClassificationBundle(classification=classification, spans=tuple(spans))
