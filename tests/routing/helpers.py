"""Helpers for Stage 5B routing tests."""

from __future__ import annotations

from halyk_agent.domain.parsing import (
    BlockKind,
    CanonicalBlock,
    CanonicalDocument,
    CanonicalPage,
    ParserIdentity,
    ParserKind,
    ParseStatus,
    block_identity,
    compute_metrics,
    document_identity,
)


def make_document(
    *,
    artifact: str = "art",
    source_file: str = "doc.pdf",
    raw_text: str,
    text_origin: str | None = None,
    ocr_backend: str | None = None,
    ocr_cfg: str | None = "ocr-cfg",
) -> CanonicalDocument:
    sha = "a" * 64
    doc_id = document_identity(artifact, sha)
    metadata: dict[str, object] = {}
    if text_origin is not None:
        metadata["text_origin"] = text_origin
    if ocr_backend is not None:
        metadata["ocr_backend"] = ocr_backend
    if ocr_cfg is not None and text_origin == "OCR":
        metadata["ocr_configuration_hash"] = ocr_cfg
    block = CanonicalBlock(
        id=block_identity(doc_id, 1, 0, BlockKind.PAGE_TEXT, raw_text, None),
        page_number=1,
        ordinal=0,
        kind=BlockKind.PAGE_TEXT,
        raw_text=raw_text,
        normalized_text=raw_text,
        char_start=0,
        char_end=len(raw_text),
        source_parser=ParserKind.PYPDF,
        metadata=metadata,
    )
    page = CanonicalPage(
        page_number=1,
        raw_text=raw_text,
        normalized_text=raw_text,
        blocks=[block],
    )
    return CanonicalDocument(
        id=doc_id,
        artifact_id=artifact,
        document_id=doc_id,
        document_version_id="v1",
        source_file=source_file,
        source_sha256=sha,
        parser=ParserIdentity(
            kind=ParserKind.PYPDF,
            package_name="pypdf",
            package_version="1",
            configuration_hash="c",
        ),
        status=ParseStatus.SUCCESS,
        pages=[page],
        metrics=compute_metrics([page]),
    )
