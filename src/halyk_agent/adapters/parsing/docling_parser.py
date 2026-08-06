"""FULL document parser adapter using Docling (lazy import)."""

from __future__ import annotations

import contextlib
import tempfile
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from halyk_agent.adapters.parsing.docling_mapping import (
    extract_docling_page_visuals,
    map_docling_document,
)
from halyk_agent.adapters.parsing.errors import (
    ParserDependencyMissingError,
    UnsupportedDocumentFormatError,
)
from halyk_agent.adapters.parsing.finalize import to_authoritative_parse_result
from halyk_agent.adapters.parsing.limits import ParserLimits
from halyk_agent.contracts.parsing import ParseRequest
from halyk_agent.domain.page_quality import ImageVisibility, PageVisualSignals
from halyk_agent.domain.parsing import (
    CanonicalDocument,
    ParseResult,
    ParserIdentity,
    ParserKind,
    ParseStatus,
    ParseWarning,
    ParseWarningCode,
    compute_metrics,
    configuration_hash,
    document_identity,
    document_version_identity,
    empty_metrics,
)


def _docling_version() -> str:
    try:
        return version("docling")
    except PackageNotFoundError:
        return "missing"


def ensure_docling_available() -> None:
    """Raise a typed error when the full extra is not installed."""
    try:
        import docling  # noqa: F401
    except ImportError as exc:  # pragma: no cover - exercised in tests via mock path
        raise ParserDependencyMissingError(
            "Docling is not installed. Install with: uv sync --extra full"
        ) from exc


class DoclingDocumentParser:
    """FULL parser wrapping Docling DocumentConverter with lazy imports."""

    def __init__(
        self,
        limits: ParserLimits | None = None,
        *,
        ocr_enabled: bool = False,
        table_structure_enabled: bool = True,
    ) -> None:
        self.limits = limits or ParserLimits()
        self.ocr_enabled = ocr_enabled
        self.table_structure_enabled = table_structure_enabled

    def parser_identity(self) -> ParserIdentity:
        """Return parser identity including configuration hash."""
        cfg = configuration_hash(
            {
                "ocr_enabled": self.ocr_enabled,
                "table_structure_enabled": self.table_structure_enabled,
                "max_pdf_pages": self.limits.max_pdf_pages,
                "max_document_characters": self.limits.max_document_characters,
                "backend": "docling",
            }
        )
        return ParserIdentity(
            kind=ParserKind.DOCLING,
            package_name="docling",
            package_version=_docling_version(),
            configuration_hash=cfg,
        )

    def supports(self, media_type: str | None, source_file: str) -> bool:
        """Return whether Docling path should attempt this file."""
        name = source_file.lower()
        if name.endswith((".pdf", ".docx")):
            return True
        mt = (media_type or "").lower()
        return mt.endswith("pdf") or "wordprocessingml" in mt or mt.endswith("docx")

    def parse_with_visuals(
        self,
        data: bytes,
        *,
        source_file: str,
        artifact_id: str,
        source_sha256: str,
        document_id: str | None = None,
        media_type: str | None = None,
        converter: Any | None = None,
    ) -> tuple[CanonicalDocument, list[PageVisualSignals]]:
        """Parse via Docling into intermediate candidate + picture visual metadata."""
        _ = document_id
        ensure_docling_available()
        if not self.supports(media_type, source_file):
            raise UnsupportedDocumentFormatError("Docling parser supports PDF and DOCX")

        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        parser = self.parser_identity()
        warnings: list[ParseWarning] = []
        started = time.perf_counter()

        active_converter: Any
        if converter is None:
            pipeline_options = PdfPipelineOptions()
            pipeline_options.do_ocr = self.ocr_enabled
            pipeline_options.do_table_structure = self.table_structure_enabled
            active_converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
                }
            )
        else:
            active_converter = converter

        suffix = Path(source_file).suffix or ".pdf"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)
        try:
            result = active_converter.convert(str(tmp_path))
            docling_doc = result.document
        except Exception as exc:
            warnings.append(
                ParseWarning(
                    code=ParseWarningCode.PARSER_ERROR,
                    message=f"Docling conversion failed: {exc.__class__.__name__}",
                )
            )
            failed = CanonicalDocument(
                id=document_identity(artifact_id, source_sha256),
                artifact_id=artifact_id,
                document_id=document_identity(artifact_id, source_sha256),
                document_version_id=document_version_identity(artifact_id, source_sha256, parser),
                source_file=source_file,
                source_sha256=source_sha256,
                mime_type=media_type,
                parser=parser,
                status=ParseStatus.FAILED,
                pages=[],
                metrics=empty_metrics(),
                warnings=warnings,
            )
            return failed, []
        finally:
            with contextlib.suppress(OSError):
                tmp_path.unlink(missing_ok=True)
            _ = started

        doc_id = document_identity(artifact_id, source_sha256)
        pages, map_warnings = map_docling_document(docling_doc, document_id=doc_id)
        warnings.extend(map_warnings)

        status = ParseStatus.SUCCESS if pages else ParseStatus.PARTIAL
        total = sum(len(page.raw_text) for page in pages)
        if total > self.limits.max_document_characters:
            status = ParseStatus.PARTIAL
            warnings.append(
                ParseWarning(
                    code=ParseWarningCode.LIMIT_EXCEEDED,
                    message="document character limit exceeded after Docling mapping",
                )
            )
        if len(pages) > self.limits.max_pdf_pages:
            pages = pages[: self.limits.max_pdf_pages]
            status = ParseStatus.PARTIAL
            warnings.append(
                ParseWarning(
                    code=ParseWarningCode.LIMIT_EXCEEDED,
                    message="PDF page limit exceeded after Docling mapping",
                )
            )

        visuals = extract_docling_page_visuals(
            docling_doc,
            page_numbers=[page.page_number for page in pages] or [1],
        )
        if not pages:
            visuals = [
                PageVisualSignals(
                    page_number=1,
                    image_count=0,
                    image_visibility=ImageVisibility.UNKNOWN,
                    extraction_warnings=["no_pages_mapped"],
                )
            ]

        metrics = compute_metrics(pages)
        document = CanonicalDocument(
            id=doc_id,
            artifact_id=artifact_id,
            document_id=doc_id,
            document_version_id=document_version_identity(artifact_id, source_sha256, parser),
            source_file=source_file,
            source_sha256=source_sha256,
            mime_type=media_type,
            parser=parser,
            status=status,
            pages=pages,
            metrics=metrics,
            warnings=warnings,
        )
        return document, visuals

    def parse_canonical(
        self,
        data: bytes,
        *,
        source_file: str,
        artifact_id: str,
        source_sha256: str,
        document_id: str | None = None,
        media_type: str | None = None,
    ) -> CanonicalDocument:
        """Parse via Docling into CanonicalDocument (intermediate; not final trust)."""
        document, _visuals = self.parse_with_visuals(
            data,
            source_file=source_file,
            artifact_id=artifact_id,
            source_sha256=source_sha256,
            document_id=document_id,
            media_type=media_type,
        )
        return document

    async def parse(self, request: ParseRequest) -> ParseResult:
        """DocumentParser Protocol: gated authoritative ParseResult."""
        import time

        data = request.source_path.read_bytes()
        started = time.perf_counter()
        candidate, visuals = self.parse_with_visuals(
            data,
            source_file=request.source_file,
            artifact_id=request.artifact_id,
            source_sha256=request.source_sha256,
            document_id=request.document_id,
            media_type=request.mime_type,
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        result, _summary = to_authoritative_parse_result(
            candidate,
            page_visuals=visuals,
            duration_ms=duration_ms,
        )
        return result
