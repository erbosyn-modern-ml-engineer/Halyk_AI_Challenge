"""Evidence helpers for document-derived identity assertions."""

from __future__ import annotations

from dataclasses import dataclass

from halyk_agent.domain.evidence import EvidenceSpan
from halyk_agent.domain.evidence_factory import create_exact_page_span
from halyk_agent.domain.ids import deterministic_id, sha256_text
from halyk_agent.domain.ocr import TextOrigin
from halyk_agent.domain.parsing import CanonicalDocument


@dataclass(frozen=True, slots=True)
class IdentitySpanResult:
    span: EvidenceSpan | None
    rejected_low_trust_ocr: bool = False


def _overlapping_block_origin(
    document: CanonicalDocument,
    *,
    page_number: int,
    char_start: int,
    char_end: int,
) -> tuple[TextOrigin, str | None, str | None]:
    page = next((p for p in document.pages if p.page_number == page_number), None)
    if page is None:
        return TextOrigin.EMBEDDED_PDF_TEXT, None, None
    best_origin = TextOrigin.EMBEDDED_PDF_TEXT
    ocr_backend: str | None = None
    ocr_cfg: str | None = None
    for block in page.blocks:
        if block.char_start is None or block.char_end is None:
            continue
        if block.char_end <= char_start or block.char_start >= char_end:
            continue
        origin_raw = block.metadata.get("text_origin")
        if origin_raw is None:
            continue
        try:
            origin = TextOrigin(str(origin_raw))
        except ValueError:
            continue
        if origin is TextOrigin.OCR:
            best_origin = TextOrigin.OCR
            backend = block.metadata.get("ocr_backend")
            cfg = block.metadata.get("ocr_configuration_hash")
            ocr_backend = str(backend) if backend is not None else None
            ocr_cfg = str(cfg) if cfg is not None else None
            break
        if (
            origin is TextOrigin.DOCLING_EXTRACTED_TEXT
            and best_origin is TextOrigin.EMBEDDED_PDF_TEXT
        ):
            best_origin = origin
    return best_origin, ocr_backend, ocr_cfg


def create_identity_span(
    document: CanonicalDocument,
    *,
    page_number: int,
    char_start: int,
    char_end: int,
) -> IdentitySpanResult:
    """
    Build an EvidenceSpan for an identity quote with inherited parser/OCR provenance.

    OCR-origin spans without backend identity are rejected (low trust).
    """
    base = create_exact_page_span(document, page_number, char_start, char_end)
    origin, ocr_backend, ocr_cfg = _overlapping_block_origin(
        document,
        page_number=page_number,
        char_start=char_start,
        char_end=char_end,
    )
    if origin is TextOrigin.OCR and not ocr_backend:
        return IdentitySpanResult(span=None, rejected_low_trust_ocr=True)

    span_id = deterministic_id(
        "evidence-span-v1",
        document.document_id,
        document.document_version_id,
        page_number,
        char_start,
        char_end,
        sha256_text(base.quote),
        "",
        "",
        "",
        "",
        origin.value,
        ocr_cfg or "",
    )
    span = EvidenceSpan(
        id=span_id,
        source_file=document.source_file,
        document_id=document.document_id,
        document_version_id=document.document_version_id,
        page_number=page_number,
        quote=base.quote,
        char_start=char_start,
        char_end=char_end,
        text_origin=origin,
        ocr_backend_identity=ocr_backend,
        ocr_configuration_hash=ocr_cfg,
    )
    return IdentitySpanResult(span=span, rejected_low_trust_ocr=False)
