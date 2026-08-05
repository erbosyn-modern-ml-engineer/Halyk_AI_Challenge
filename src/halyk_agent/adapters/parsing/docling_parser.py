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
from halyk_agent.contracts.parsing import ParsedDocument
from halyk_agent.domain.evidence_factory import build_evidence_catalog
from halyk_agent.domain.parsing import (
    CanonicalDocument,
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

    async def parse(
        self,
        data: bytes,
        *,
        source_file: str,
        document_id: str,
        media_type: str | None = None,
    ) -> ParsedDocument:
        """DocumentParser protocol adapter."""
        from halyk_agent.adapters.archive.hashing import sha256_bytes

        canonical = self.parse_canonical(
            data,
            source_file=source_file,
            artifact_id=document_id,
            source_sha256=sha256_bytes(data),
            document_id=document_id,
            media_type=media_type,
        )
        text = "\n\n".join(page.raw_text for page in canonical.pages) or " "
        spans = (
            build_evidence_catalog(canonical) if canonical.status is not ParseStatus.FAILED else []
        )
        return ParsedDocument(
            document_id=canonical.document_id,
            source_file=source_file,
            page_count=len(canonical.pages),
            text=text if text.strip() else " ",
            tables=[],
            spans=spans,
            metadata={"status": canonical.status.value, "parser": canonical.parser.kind.value},
        )
