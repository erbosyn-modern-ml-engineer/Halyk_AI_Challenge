"""FULL document parser adapter using Docling (lazy import)."""

from __future__ import annotations

import contextlib
import tempfile
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from halyk_agent.adapters.parsing.docling_mapping import map_docling_document
from halyk_agent.adapters.parsing.errors import (
    ParserDependencyMissingError,
    UnsupportedDocumentFormatError,
)
from halyk_agent.adapters.parsing.limits import ParserLimits
from halyk_agent.contracts.parsing import ParseRequest
from halyk_agent.domain.parsing import (
    CanonicalDocument,
    ParseAttempt,
    ParseResult,
    ParserIdentity,
    ParserKind,
    ParseStatus,
    ParseWarning,
    ParseWarningCode,
    QualityDecision,
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
        """Parse via Docling into CanonicalDocument."""
        _ = document_id
        ensure_docling_available()
        if not self.supports(media_type, source_file):
            raise UnsupportedDocumentFormatError("Docling parser supports PDF and DOCX")

        # Lazy imports — never at module import time.
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        parser = self.parser_identity()
        warnings: list[ParseWarning] = []
        started = time.perf_counter()

        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = self.ocr_enabled
        pipeline_options.do_table_structure = self.table_structure_enabled

        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
            }
        )

        suffix = Path(source_file).suffix or ".pdf"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)
        try:
            result = converter.convert(str(tmp_path))
            docling_doc = result.document
        except Exception as exc:
            warnings.append(
                ParseWarning(
                    code=ParseWarningCode.PARSER_ERROR,
                    message=f"Docling conversion failed: {exc.__class__.__name__}",
                )
            )
            return CanonicalDocument(
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
        finally:
            with contextlib.suppress(OSError):
                tmp_path.unlink(missing_ok=True)
            _ = started

        doc_id = document_identity(artifact_id, source_sha256)
        pages, map_warnings = map_docling_document(docling_doc, document_id=doc_id)
        warnings.extend(map_warnings)

        # Enforce document character limit.
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

        metrics = compute_metrics(pages)
        return CanonicalDocument(
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

    def _to_parse_result(
        self,
        document: CanonicalDocument,
        *,
        duration_ms: int = 0,
        quality_decision: QualityDecision | None = None,
    ) -> ParseResult:
        """Wrap a CanonicalDocument as the authoritative ParseResult."""
        if quality_decision is None:
            if document.status is ParseStatus.SUCCESS:
                quality_decision = QualityDecision.ACCEPT
            elif document.status is ParseStatus.PARTIAL:
                quality_decision = QualityDecision.HUMAN_REVIEW_REQUIRED
            elif document.status in {ParseStatus.ENCRYPTED, ParseStatus.UNSUPPORTED}:
                quality_decision = QualityDecision.REJECT
            else:
                quality_decision = QualityDecision.HUMAN_REVIEW_REQUIRED
        attempt = ParseAttempt(
            parser=document.parser,
            status=document.status,
            metrics=document.metrics,
            warnings=list(document.warnings),
            duration_ms=duration_ms,
        )
        return ParseResult(
            artifact_id=document.artifact_id,
            selected_document=document,
            attempts=[attempt],
            quality_decision=quality_decision,
            cache_hit=False,
        )

    async def parse(self, request: ParseRequest) -> ParseResult:
        """DocumentParser Protocol: parse from request.source_path."""
        import time

        data = request.source_path.read_bytes()
        started = time.perf_counter()
        document = self.parse_canonical(
            data,
            source_file=request.source_file,
            artifact_id=request.artifact_id,
            source_sha256=request.source_sha256,
            document_id=request.document_id,
            media_type=request.mime_type,
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        return self._to_parse_result(document, duration_ms=duration_ms)
