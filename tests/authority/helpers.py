"""Helpers for Stage 5C authority tests."""

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
from halyk_agent.domain.routing.models import (
    DocumentEntityLink,
    ResolutionConfidence,
    ResolutionMethod,
)


def make_document(
    *,
    artifact: str = "art",
    source_file: str = "doc.pdf",
    raw_text: str,
    sha: str | None = None,
) -> CanonicalDocument:
    digest = sha or ("a" * 64)
    doc_id = document_identity(artifact, digest)
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
        metadata={},
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
        source_sha256=digest,
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


def make_link(
    document: CanonicalDocument,
    *,
    scenario_ids: tuple[str, ...] = ("P1",),
    group_document: bool = False,
) -> DocumentEntityLink:
    return DocumentEntityLink(
        document_id=document.document_id,
        document_version_id=document.document_version_id,
        scenario_ids=scenario_ids,
        account_ids=(),
        method=ResolutionMethod.NORMALIZED_LEGAL_NAME,
        confidence=ResolutionConfidence.DERIVED,
        evidence_span_ids=(),
        group_document=group_document,
    )
