"""Provenance-preserving merge of validated OCR into a new canonical revision."""

from __future__ import annotations

from typing import cast

from halyk_agent.adapters.parsing.post_parse_gate import apply_post_parse_quality_gate
from halyk_agent.adapters.parsing.text_normalization import normalize_text
from halyk_agent.domain.common import JsonObject
from halyk_agent.domain.ids import deterministic_id, sha256_text
from halyk_agent.domain.ocr import OcrPageResult, OcrPageStatus, TextOrigin, validate_ocr_page_text
from halyk_agent.domain.page_quality import is_blocking_page_quality
from halyk_agent.domain.parsing import (
    BlockKind,
    CanonicalBlock,
    CanonicalBoundingBox,
    CanonicalDocument,
    CanonicalPage,
    CoordinateOrigin,
    ParserKind,
    ParseStatus,
    compute_metrics,
    document_version_identity,
)


def _bbox_from_tuple(
    bbox: tuple[float, float, float, float] | None,
    *,
    page_width: float,
    page_height: float,
) -> CanonicalBoundingBox | None:
    if bbox is None:
        return None
    left, top, right, bottom = bbox
    left = max(0.0, min(left, page_width))
    right = max(0.0, min(right, page_width))
    top = max(0.0, min(top, page_height))
    bottom = max(0.0, min(bottom, page_height))
    if not (left < right and top < bottom):
        return None
    try:
        return CanonicalBoundingBox(
            left=left,
            top=top,
            right=right,
            bottom=bottom,
            page_width=page_width,
            page_height=page_height,
            origin=CoordinateOrigin.TOP_LEFT,
        )
    except ValueError:
        return None


def _merge_page(
    document: CanonicalDocument,
    page: CanonicalPage,
    result: OcrPageResult,
) -> CanonicalPage:
    """Merge validated OCR blocks into one page without erasing embedded text."""
    ordered = sorted(result.blocks, key=lambda item: item.reading_order)
    ocr_texts = [block.text.strip() for block in ordered if block.text.strip()]
    if not ocr_texts:
        return page
    combined = "\n".join(ocr_texts)
    if validate_ocr_page_text(combined) is not OcrPageStatus.OCR_SUCCEEDED:
        return page

    segments: list[tuple[str, JsonObject, CanonicalBoundingBox | None]] = []
    if page.raw_text.strip():
        segments.append(
            (
                page.raw_text,
                cast(JsonObject, {"text_origin": TextOrigin.EMBEDDED_PDF_TEXT.value}),
                None,
            )
        )
    page_w = float(page.width or 1000.0)
    page_h = float(page.height or 1000.0)
    for ocr_block in ordered:
        text = ocr_block.text.strip()
        if not text:
            continue
        segments.append(
            (
                text,
                cast(
                    JsonObject,
                    {
                        "text_origin": TextOrigin.OCR.value,
                        "ocr_backend": ocr_block.backend.identity_token(),
                        "ocr_configuration_hash": ocr_block.backend.configuration_hash,
                        "source_image_identity": ocr_block.source_image_identity,
                        "confidence": ocr_block.confidence,
                    },
                ),
                _bbox_from_tuple(ocr_block.bbox, page_width=page_w, page_height=page_h),
            )
        )

    raw_parts: list[str] = []
    canonical_blocks: list[CanonicalBlock] = []
    cursor = 0
    for ordinal, (text, meta, bbox) in enumerate(segments):
        if ordinal > 0:
            raw_parts.append("\n")
            cursor += 1
        start = cursor
        raw_parts.append(text)
        end = start + len(text)
        cursor = end
        origin = str(meta.get("text_origin"))
        source_parser = (
            page.blocks[0].source_parser
            if page.blocks and origin == TextOrigin.EMBEDDED_PDF_TEXT.value
            else ParserKind.PYPDF
        )
        canonical_blocks.append(
            CanonicalBlock(
                id=deterministic_id(
                    "block",
                    document.document_id,
                    page.page_number,
                    ordinal,
                    origin,
                    sha256_text(text),
                    str(meta.get("ocr_configuration_hash", "")),
                ),
                page_number=page.page_number,
                ordinal=ordinal,
                kind=BlockKind.PAGE_TEXT,
                raw_text=text,
                normalized_text=normalize_text(text),
                char_start=start,
                char_end=end,
                bbox=bbox,
                source_parser=source_parser,
                metadata=meta,
            )
        )
    raw = "".join(raw_parts)
    # Exact quote invariant: each block substring must match.
    for canonical in canonical_blocks:
        if canonical.char_start is None or canonical.char_end is None:
            raise ValueError("OCR merge missing block offsets")
        if raw[canonical.char_start : canonical.char_end] != canonical.raw_text:
            raise ValueError("OCR merge violated exact quote invariant")
    return page.model_copy(
        update={
            "raw_text": raw,
            "normalized_text": normalize_text(raw),
            "blocks": canonical_blocks,
        }
    )


def merge_ocr_into_document(
    document: CanonicalDocument,
    page_results: list[OcrPageResult],
) -> tuple[CanonicalDocument, int]:
    """Build a new canonical revision with OCR blocks; never overwrite embedded text.

    Synthetic failure messages are never merged as document text.
    Returns (enriched_document, remaining_blocking_page_count).
    """
    by_page = {result.request.page_number: result for result in page_results}
    new_pages: list[CanonicalPage] = []
    ocr_tokens: set[str] = set()

    for page in document.pages:
        result = by_page.get(page.page_number)
        if result is None or result.status is not OcrPageStatus.OCR_SUCCEEDED:
            new_pages.append(page)
            continue
        # Never merge diagnostic failure strings.
        if (
            result.message
            and result.message.strip().lower()
            in {
                "ocr failed",
                "page unreadable",
                "backend unavailable",
            }
            and not result.blocks
        ):
            new_pages.append(page)
            continue
        merged = _merge_page(document, page, result)
        if merged is not page:
            for block in result.blocks:
                ocr_tokens.add(block.backend.configuration_hash)
        new_pages.append(merged)

    parser = document.parser
    if ocr_tokens:
        version_id = document_version_identity(
            document.artifact_id,
            document.source_sha256,
            parser.model_copy(
                update={
                    "configuration_hash": deterministic_id(
                        parser.configuration_hash,
                        "ocr",
                        *sorted(ocr_tokens),
                    )[:32]
                }
            ),
        )
    else:
        version_id = document.document_version_id

    candidate = document.model_copy(
        update={
            "document_version_id": version_id,
            "pages": new_pages,
            "metrics": compute_metrics(new_pages),
            "status": ParseStatus.SUCCESS,
        }
    )
    gated = apply_post_parse_quality_gate(candidate)
    remaining = sum(1 for state in gated.summary.page_states if is_blocking_page_quality(state))
    return gated.document, remaining
