"""Deterministic document metadata extraction (Stage 5C)."""

# ruff: noqa: RUF001

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from halyk_agent.domain.authority.constants import SUPERSESSION_BANNER_PATTERNS
from halyk_agent.domain.authority.evidence import find_first_span, find_status_banner_span
from halyk_agent.domain.authority.models import DocumentMetadata
from halyk_agent.domain.evidence import EvidenceSpan
from halyk_agent.domain.parsing import CanonicalDocument


@dataclass(frozen=True, slots=True)
class MetadataBundle:
    metadata: DocumentMetadata
    spans: tuple[EvidenceSpan, ...]


_DATE_RE = re.compile(
    r"(?i)(?:от\s+|dated\s+|date\s*[:=]?\s*|as of\s+|effective\s+(?:date\s*)?[:=]?\s*)?"
    r"("
    r"\d{1,2}\s+[A-Za-zА-Яа-яЁёҚқҒғҮүҰұҢңӘәІіӨөҺһ]+\s+\d{4}"
    r"|\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}[./]\d{1,2}[./]\d{4}"
    r")"
)

_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_DMY_DATE = re.compile(r"(\d{1,2})[./](\d{1,2})[./](\d{4})")

_AGREEMENT_NO = re.compile(
    r"(?i)(?:займ|заём|loan|facility|agreement|договор)[^\n]{0,40}?"
    r"(?:№|no\.?|#)\s*([A-Z]{2,5}-?\d[\w-]*)"
)
_ACC_NO = re.compile(r"(?<![A-Za-z0-9-])(ACC-\d+)(?![A-Za-z0-9-])")
_VERSION = re.compile(r"(?i)(?:редакция|revision|version|v)\s*(?:№\s*)?([vV]?\d[\w.-]*)")
_REPORT_NO = re.compile(
    r"(?i)(?:аудиторское дело|audit\s*(?:file|engagement)|report)\s*№?\s*([A-Z]{0,5}-?\d[\w-]*)"
)


def _normalize_date_token(raw: str) -> str | None:
    text = " ".join(raw.split())
    iso = _ISO_DATE.fullmatch(text)
    if iso:
        try:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3))).isoformat()
        except ValueError:
            return None
    dmy = _DMY_DATE.fullmatch(text)
    if dmy:
        try:
            return date(int(dmy.group(3)), int(dmy.group(2)), int(dmy.group(1))).isoformat()
        except ValueError:
            return None
    # Keep human-readable Russian/English month forms as opaque typed strings.
    if len(text) >= 8:
        return text
    return None


def _first_page_text(document: CanonicalDocument, *, max_chars: int = 2500) -> str:
    if not document.pages:
        return ""
    return (document.pages[0].raw_text or "")[:max_chars]


def _title_from_head(head: str) -> str | None:
    lines = [ln.strip() for ln in head.splitlines() if ln.strip()]
    if not lines:
        return None
    # Prefer an explicit titled line among the first few non-empty lines.
    for line in lines[:8]:
        low = line.casefold()
        if any(
            token in low
            for token in (
                "договор",
                "agreement",
                "report",
                "отчёт",
                "отчет",
                "kyc",
                "пресс",
                "press",
                "меморандум",
                "memorandum",
                "руководство",
                "consolidated",
            )
        ):
            return line[:240]
    return lines[0][:240]


def extract_metadata(document: CanonicalDocument) -> DocumentMetadata:
    """Extract typed metadata with optional evidence span IDs."""
    return extract_metadata_bundle(document).metadata


def extract_metadata_bundle(document: CanonicalDocument) -> MetadataBundle:
    """Extract typed metadata and persistable evidence spans."""
    head = _first_page_text(document)
    blob = "\n".join(page.raw_text or "" for page in document.pages)
    spans: list[EvidenceSpan] = []
    span_ids: list[str] = []

    def _remember(span: EvidenceSpan | None) -> None:
        if span is None or span.id in span_ids:
            return
        spans.append(span)
        span_ids.append(span.id)

    title = _title_from_head(head)
    if title:
        hit = find_first_span(document, pattern=title[:80] if len(title) > 80 else title)
        if hit.span is not None:
            _remember(hit.span)
            title = hit.span.quote

    agreement_number: str | None = None
    m = _AGREEMENT_NO.search(head) or _ACC_NO.search(head)
    if m:
        agreement_number = m.group(1) if m.lastindex else m.group(0)
        hit = find_first_span(document, pattern=agreement_number)
        _remember(hit.span)

    version_indicator: str | None = None
    vm = _VERSION.search(head)
    if vm:
        version_indicator = vm.group(0).strip()[:80]
        hit = find_first_span(document, pattern=version_indicator[:40])
        _remember(hit.span)

    report_number: str | None = None
    rm = _REPORT_NO.search(head)
    if rm:
        report_number = rm.group(0).strip()[:100]
        hit = find_first_span(document, pattern=report_number[:40])
        _remember(hit.span)

    document_date: str | None = None
    effective_date: str | None = None
    execution_date: str | None = None
    report_date: str | None = None
    for match in _DATE_RE.finditer(head):
        token = _normalize_date_token(match.group(1))
        if token is None:
            continue
        context = head[max(0, match.start() - 40) : match.end() + 20].casefold()
        if document_date is None:
            document_date = token
        if "effective" in context or "вступлен" in context or "күшіне" in context:
            effective_date = token
        if "executed" in context or "исполнительн" in context or "подписан" in context:
            execution_date = token
        if "report" in context or "отчёт" in context or "отчет" in context:
            report_date = token

    draft_final_marker: str | None = None
    superseded_marker: str | None = None
    # Supersession is a document-status banner, never running covenant prose.
    for marker in SUPERSESSION_BANNER_PATTERNS:
        span = find_status_banner_span(document, patterns=(marker,))
        if span is None:
            continue
        _remember(span)
        superseded_marker = span.quote
        break
    for marker in (
        "FINAL",
        "окончательн",
        "DRAFT",
        "черновик",
        "ПРОЕКТ",
        "preliminary",
        "предварительн",
        "ИСПОЛНИТЕЛЬНЫЙ ЭКЗЕМПЛЯР",
        "CURRENT",
    ):
        hit = find_first_span(document, pattern=marker)
        if hit.span is None:
            continue
        _remember(hit.span)
        if draft_final_marker is None:
            draft_final_marker = hit.span.quote

    period_covered: str | None = None
    pm = re.search(
        r"(?i)(?:financial year ended|год[,\s]+закончивш\w*|period ended)\s*([^\n]{5,60})",
        blob[:3000],
    )
    if pm:
        period_covered = pm.group(0).strip()[:120]

    metadata = DocumentMetadata(
        document_id=document.document_id,
        document_version_id=document.document_version_id,
        source_file=document.source_file,
        source_sha256=document.source_sha256,
        title=title,
        document_date=document_date,
        effective_date=effective_date,
        execution_date=execution_date,
        report_date=report_date,
        period_covered=period_covered,
        version_indicator=version_indicator,
        report_number=report_number,
        agreement_number=agreement_number,
        draft_final_marker=draft_final_marker,
        superseded_marker=superseded_marker,
        evidence_span_ids=tuple(span_ids),
    )
    return MetadataBundle(metadata=metadata, spans=tuple(spans))
