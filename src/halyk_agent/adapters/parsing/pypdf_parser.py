"""FAST PDF/TXT parser backed by pypdf."""

from __future__ import annotations

import io
import time
from importlib.metadata import PackageNotFoundError, version
from typing import BinaryIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from halyk_agent.adapters.parsing.errors import (
    UnsupportedDocumentFormatError,
)
from halyk_agent.adapters.parsing.limits import ParserLimits
from halyk_agent.adapters.parsing.text_normalization import normalize_text
from halyk_agent.contracts.parsing import ParseRequest
from halyk_agent.domain.parsing import (
    BlockKind,
    CanonicalBlock,
    CanonicalDocument,
    CanonicalPage,
    ParseAttempt,
    ParseMetrics,
    ParseResult,
    ParserIdentity,
    ParserKind,
    ParseStatus,
    ParseWarning,
    ParseWarningCode,
    QualityDecision,
    block_identity,
    compute_metrics,
    configuration_hash,
    document_identity,
    document_version_identity,
    empty_metrics,
)


def _pypdf_version() -> str:
    try:
        return version("pypdf")
    except PackageNotFoundError:  # pragma: no cover
        return "unknown"


def pypdf_parser_identity(limits: ParserLimits) -> ParserIdentity:
    """Return ParserIdentity for the pypdf FAST backend."""
    cfg = configuration_hash(
        {
            "max_pdf_pages": limits.max_pdf_pages,
            "max_page_characters": limits.max_page_characters,
            "max_document_characters": limits.max_document_characters,
            "max_parser_warnings": limits.max_parser_warnings,
            "backend": "pypdf",
        }
    )
    return ParserIdentity(
        kind=ParserKind.PYPDF,
        package_name="pypdf",
        package_version=_pypdf_version(),
        configuration_hash=cfg,
    )


def plain_text_parser_identity(limits: ParserLimits) -> ParserIdentity:
    """Return ParserIdentity for the plain-text FAST backend."""
    cfg = configuration_hash(
        {
            "max_document_characters": limits.max_document_characters,
            "backend": "plain_text",
        }
    )
    return ParserIdentity(
        kind=ParserKind.PLAIN_TEXT,
        package_name="halyk_agent",
        package_version="0.1.0",
        configuration_hash=cfg,
    )


def _trim_warnings(
    warnings: list[ParseWarning],
    limit: int,
) -> list[ParseWarning]:
    if len(warnings) <= limit:
        return warnings
    trimmed = warnings[:limit]
    trimmed.append(
        ParseWarning(
            code=ParseWarningCode.LIMIT_EXCEEDED,
            message="parser warning limit reached; additional warnings omitted",
        )
    )
    return trimmed


def _page_from_text(
    *,
    document_id: str,
    page_number: int,
    raw_text: str,
    source_parser: ParserKind,
    warnings: list[ParseWarning],
) -> CanonicalPage:
    normalized = normalize_text(raw_text)
    blocks: list[CanonicalBlock] = []
    if raw_text:
        block = CanonicalBlock(
            id=block_identity(
                document_id,
                page_number,
                0,
                BlockKind.PAGE_TEXT,
                raw_text,
                None,
            ),
            page_number=page_number,
            ordinal=0,
            kind=BlockKind.PAGE_TEXT,
            raw_text=raw_text,
            normalized_text=normalized,
            char_start=0,
            char_end=len(raw_text),
            bbox=None,
            source_parser=source_parser,
        )
        blocks.append(block)
    else:
        warnings.append(
            ParseWarning(
                code=ParseWarningCode.EMPTY_PAGE,
                message="empty page",
                page_number=page_number,
            )
        )
    return CanonicalPage(
        page_number=page_number,
        width=None,
        height=None,
        raw_text=raw_text,
        normalized_text=normalized,
        blocks=blocks,
        tables=[],
        warnings=[w.message for w in warnings if w.page_number == page_number],
    )


def _build_document(
    *,
    artifact_id: str,
    source_file: str,
    source_sha256: str,
    mime_type: str | None,
    parser: ParserIdentity,
    status: ParseStatus,
    pages: list[CanonicalPage],
    warnings: list[ParseWarning],
    metrics: ParseMetrics | None = None,
) -> CanonicalDocument:
    doc_id = document_identity(artifact_id, source_sha256)
    version_id = document_version_identity(artifact_id, source_sha256, parser)
    if status is ParseStatus.FAILED:
        pages = []
        metrics = empty_metrics()
    elif metrics is None:
        metrics = compute_metrics(pages)
    return CanonicalDocument(
        id=doc_id,
        artifact_id=artifact_id,
        document_id=doc_id,
        document_version_id=version_id,
        source_file=source_file,
        source_sha256=source_sha256,
        mime_type=mime_type,
        parser=parser,
        status=status,
        pages=pages,
        metrics=metrics,
        warnings=warnings,
    )


class PyPdfDocumentParser:
    """FAST parser for PDF and plain text using pypdf where applicable."""

    def __init__(self, limits: ParserLimits | None = None) -> None:
        self.limits = limits or ParserLimits()

    def supports(self, media_type: str | None, source_file: str) -> bool:
        """Return whether this parser can handle the given input."""
        name = source_file.lower()
        if name.endswith(".pdf") or (media_type or "").endswith("pdf"):
            return True
        media = media_type or ""
        return bool(name.endswith(".txt") or media in {"text/plain", "text/plain; charset=utf-8"})

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
        """Parse bytes into a CanonicalDocument."""
        _ = document_id  # Stage 1 contract compatibility; identity is derived.
        name = source_file.lower()
        if name.endswith(".txt") or (media_type or "").startswith("text/plain"):
            return self._parse_plain_text(
                data,
                source_file=source_file,
                artifact_id=artifact_id,
                source_sha256=source_sha256,
                media_type=media_type or "text/plain",
            )
        if name.endswith(".pdf") or (media_type or "").endswith("pdf"):
            return self._parse_pdf(
                data,
                source_file=source_file,
                artifact_id=artifact_id,
                source_sha256=source_sha256,
                media_type=media_type or "application/pdf",
            )
        raise UnsupportedDocumentFormatError("FAST parser supports PDF and TXT only")

    def _parse_plain_text(
        self,
        data: bytes,
        *,
        source_file: str,
        artifact_id: str,
        source_sha256: str,
        media_type: str,
    ) -> CanonicalDocument:
        parser = plain_text_parser_identity(self.limits)
        warnings: list[ParseWarning] = []
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("utf-8", errors="replace")
            warnings.append(
                ParseWarning(
                    code=ParseWarningCode.PARTIAL_CONTENT,
                    message="plain text decoded with replacement characters",
                )
            )
        if "\x00" in text:
            text = text.replace("\x00", "")
            warnings.append(
                ParseWarning(
                    code=ParseWarningCode.PARTIAL_CONTENT,
                    message="NUL characters removed from plain text",
                )
            )
        status = ParseStatus.SUCCESS
        if len(text) > self.limits.max_document_characters:
            text = text[: self.limits.max_document_characters]
            status = ParseStatus.PARTIAL
            warnings.append(
                ParseWarning(
                    code=ParseWarningCode.LIMIT_EXCEEDED,
                    message="document character limit exceeded",
                )
            )
        page = _page_from_text(
            document_id=document_identity(artifact_id, source_sha256),
            page_number=1,
            raw_text=text,
            source_parser=ParserKind.PLAIN_TEXT,
            warnings=warnings,
        )
        return _build_document(
            artifact_id=artifact_id,
            source_file=source_file,
            source_sha256=source_sha256,
            mime_type=media_type,
            parser=parser,
            status=status if text.strip() else ParseStatus.PARTIAL,
            pages=[page],
            warnings=_trim_warnings(warnings, self.limits.max_parser_warnings),
        )

    def _parse_pdf(
        self,
        data: bytes,
        *,
        source_file: str,
        artifact_id: str,
        source_sha256: str,
        media_type: str,
    ) -> CanonicalDocument:
        parser = pypdf_parser_identity(self.limits)
        warnings: list[ParseWarning] = []
        started = time.perf_counter()
        _ = started
        try:
            reader = PdfReader(io.BytesIO(data), strict=False)
        except PdfReadError as exc:
            warnings.append(
                ParseWarning(
                    code=ParseWarningCode.PARSER_ERROR,
                    message=f"malformed PDF: {exc.__class__.__name__}",
                )
            )
            return _build_document(
                artifact_id=artifact_id,
                source_file=source_file,
                source_sha256=source_sha256,
                mime_type=media_type,
                parser=parser,
                status=ParseStatus.FAILED,
                pages=[],
                warnings=warnings,
            )
        except Exception as exc:
            warnings.append(
                ParseWarning(
                    code=ParseWarningCode.PARSER_ERROR,
                    message=f"PDF open failed: {exc.__class__.__name__}",
                )
            )
            return _build_document(
                artifact_id=artifact_id,
                source_file=source_file,
                source_sha256=source_sha256,
                mime_type=media_type,
                parser=parser,
                status=ParseStatus.FAILED,
                pages=[],
                warnings=warnings,
            )

        if getattr(reader, "is_encrypted", False):
            # Do not brute-force passwords.
            unlocked: object = 0
            try:
                unlocked = reader.decrypt("")
            except Exception:
                unlocked = 0
            if not unlocked:
                warnings.append(
                    ParseWarning(
                        code=ParseWarningCode.ENCRYPTED,
                        message="encrypted PDF rejected",
                    )
                )
                return _build_document(
                    artifact_id=artifact_id,
                    source_file=source_file,
                    source_sha256=source_sha256,
                    mime_type=media_type,
                    parser=parser,
                    status=ParseStatus.ENCRYPTED,
                    pages=[],
                    warnings=warnings,
                )

        page_count = len(reader.pages)
        status = ParseStatus.SUCCESS
        if page_count > self.limits.max_pdf_pages:
            status = ParseStatus.PARTIAL
            warnings.append(
                ParseWarning(
                    code=ParseWarningCode.LIMIT_EXCEEDED,
                    message="PDF page limit exceeded",
                )
            )
            page_count = self.limits.max_pdf_pages

        doc_id = document_identity(artifact_id, source_sha256)
        pages: list[CanonicalPage] = []
        total_chars = 0
        for index in range(page_count):
            page_number = index + 1
            page_warnings: list[ParseWarning] = []
            try:
                page_obj = reader.pages[index]
                extracted = page_obj.extract_text()
            except Exception:
                extracted = None
                page_warnings.append(
                    ParseWarning(
                        code=ParseWarningCode.MALFORMED_PAGE,
                        message="malformed page during text extraction",
                        page_number=page_number,
                    )
                )
                warnings.extend(page_warnings)
                status = ParseStatus.PARTIAL

            if extracted is None:
                page_warnings.append(
                    ParseWarning(
                        code=ParseWarningCode.EXTRACT_TEXT_NONE,
                        message="extract_text returned None",
                        page_number=page_number,
                    )
                )
                warnings.extend(page_warnings)
                raw_text = ""
            else:
                raw_text = extracted

            if len(raw_text) > self.limits.max_page_characters:
                raw_text = raw_text[: self.limits.max_page_characters]
                status = ParseStatus.PARTIAL
                warnings.append(
                    ParseWarning(
                        code=ParseWarningCode.LIMIT_EXCEEDED,
                        message="page character limit exceeded",
                        page_number=page_number,
                    )
                )

            if total_chars + len(raw_text) > self.limits.max_document_characters:
                remaining = self.limits.max_document_characters - total_chars
                if remaining <= 0:
                    status = ParseStatus.PARTIAL
                    warnings.append(
                        ParseWarning(
                            code=ParseWarningCode.LIMIT_EXCEEDED,
                            message="document character limit exceeded",
                            page_number=page_number,
                        )
                    )
                    break
                raw_text = raw_text[:remaining]
                status = ParseStatus.PARTIAL
                warnings.append(
                    ParseWarning(
                        code=ParseWarningCode.LIMIT_EXCEEDED,
                        message="document character limit exceeded",
                        page_number=page_number,
                    )
                )

            total_chars += len(raw_text)
            pages.append(
                _page_from_text(
                    document_id=doc_id,
                    page_number=page_number,
                    raw_text=raw_text,
                    source_parser=ParserKind.PYPDF,
                    warnings=page_warnings,
                )
            )
            if status is ParseStatus.PARTIAL and total_chars >= self.limits.max_document_characters:
                break

        if not pages and status is ParseStatus.SUCCESS:
            status = ParseStatus.PARTIAL

        return _build_document(
            artifact_id=artifact_id,
            source_file=source_file,
            source_sha256=source_sha256,
            mime_type=media_type,
            parser=parser,
            status=status,
            pages=pages,
            warnings=_trim_warnings(warnings, self.limits.max_parser_warnings),
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


def open_pdf_stream(stream: BinaryIO) -> PdfReader:
    """Helper for tests: open a PDF stream with pypdf."""
    return PdfReader(stream, strict=False)
