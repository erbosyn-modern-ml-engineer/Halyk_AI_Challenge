"""Batch document parsing application service."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from halyk_agent.adapters.archive.hashing import sha256_file
from halyk_agent.adapters.parsing.cache import LocalParseCache
from halyk_agent.adapters.parsing.errors import (
    DocumentParsingError,
    ParserDependencyMissingError,
    UnsupportedDocumentFormatError,
)
from halyk_agent.adapters.parsing.limits import ParserLimits
from halyk_agent.adapters.parsing.pypdf_parser import PyPdfDocumentParser
from halyk_agent.adapters.parsing.quality import (
    DeterministicParseQualityGate,
    QualityThresholds,
)
from halyk_agent.config import Settings, get_settings
from halyk_agent.domain.datasets import (
    ArtifactFormat,
    ArtifactRole,
    DatasetArtifact,
    DatasetManifest,
)
from halyk_agent.domain.evidence_factory import build_evidence_catalog
from halyk_agent.domain.parsing import (
    CanonicalDocument,
    ParseAttempt,
    ParseBatchReport,
    ParseResult,
    ParserIdentity,
    ParseStatus,
    ParseWarning,
    ParseWarningCode,
    QualityDecision,
    empty_metrics,
)

SUPPORTED_PARSE_FORMATS = {
    ArtifactFormat.PDF,
    ArtifactFormat.TXT,
    ArtifactFormat.DOCX,
}


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _dump_json(model: object) -> str:
    payload = model.model_dump(mode="json")  # type: ignore[attr-defined]
    return json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def _limits_from_settings(settings: Settings) -> ParserLimits:
    return ParserLimits(
        max_pdf_pages=settings.max_pdf_pages,
        max_page_characters=settings.max_page_characters,
        max_document_characters=settings.max_document_characters,
        max_parser_warnings=settings.max_parser_warnings,
    )


def _thresholds_from_settings(settings: Settings) -> QualityThresholds:
    return QualityThresholds(
        min_total_characters=settings.quality_min_total_characters,
        max_empty_page_ratio=settings.quality_max_empty_page_ratio,
        max_replacement_character_ratio=settings.quality_max_replacement_character_ratio,
        min_alphanumeric_character_ratio=settings.quality_min_alphanumeric_ratio,
        max_control_character_count=settings.quality_max_control_character_count,
        max_duplicate_line_ratio=settings.quality_max_duplicate_line_ratio,
        max_pages_without_text_ratio=settings.quality_max_pages_without_text_ratio,
    )


def select_parse_candidates(manifest: DatasetManifest) -> list[DatasetArtifact]:
    """Select artifacts that are documents or supported document formats."""
    selected = []
    for artifact in manifest.artifacts:
        if artifact.role is ArtifactRole.DOCUMENT:
            selected.append(artifact)
            continue
        if artifact.format in SUPPORTED_PARSE_FORMATS:
            selected.append(artifact)
    return sorted(selected, key=lambda item: item.id)


def _attempt_from_document(
    document: CanonicalDocument,
    *,
    duration_ms: int,
    error_code: str | None = None,
    error_message: str | None = None,
) -> ParseAttempt:
    return ParseAttempt(
        parser=document.parser,
        status=document.status,
        metrics=document.metrics,
        warnings=list(document.warnings),
        error_code=error_code,
        error_message=error_message,
        duration_ms=duration_ms,
    )


def _failed_result(
    *,
    artifact_id: str,
    parser: ParserIdentity,
    status: ParseStatus,
    decision: QualityDecision,
    message: str,
    code: str,
    duration_ms: int,
) -> ParseResult:
    attempt = ParseAttempt(
        parser=parser,
        status=status,
        metrics=empty_metrics(),
        warnings=[ParseWarning(code=ParseWarningCode.PARSER_ERROR, message=message)],
        error_code=code,
        error_message=message,
        duration_ms=duration_ms,
    )
    return ParseResult(
        artifact_id=artifact_id,
        selected_document=None,
        attempts=[attempt],
        quality_decision=decision,
        cache_hit=False,
    )


def parse_inspection_directory(
    inspection_dir: Path,
    output_dir: Path,
    *,
    profile: str,
    overwrite: bool = False,
    force_docling: bool = False,
    settings: Settings | None = None,
) -> ParseBatchReport:
    """Parse document candidates from a Stage 2 inspection directory."""
    resolved = settings or get_settings()
    profile_norm = profile.lower().strip()
    if profile_norm not in {"fast", "full"}:
        raise DocumentParsingError("profile must be fast or full")
    if force_docling and profile_norm != "full":
        raise DocumentParsingError("--force-docling is only valid for FULL profile")

    inspection_dir = inspection_dir.resolve()
    output_dir = output_dir.resolve()
    manifest_path = inspection_dir / "manifest.json"
    extracted_dir = inspection_dir / "extracted"
    if not manifest_path.is_file():
        raise DocumentParsingError("inspection manifest.json is missing")
    if not extracted_dir.is_dir():
        raise DocumentParsingError("inspection extracted/ directory is missing")

    try:
        manifest = DatasetManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise DocumentParsingError("structurally invalid Stage 2 manifest") from exc

    if output_dir.exists():
        non_empty = any(output_dir.iterdir())
        if non_empty and not overwrite:
            raise DocumentParsingError("output directory is not empty; pass --overwrite to replace")
    output_dir.mkdir(parents=True, exist_ok=True)
    documents_dir = output_dir / "documents"
    documents_dir.mkdir(parents=True, exist_ok=True)
    cache = LocalParseCache(output_dir / ".parse_cache")

    limits = _limits_from_settings(resolved)
    gate = DeterministicParseQualityGate(_thresholds_from_settings(resolved))
    fast_parser = PyPdfDocumentParser(limits=limits)

    docling_parser = None
    if profile_norm == "full":
        from halyk_agent.adapters.parsing.docling_parser import DoclingDocumentParser

        docling_parser = DoclingDocumentParser(
            limits=limits,
            ocr_enabled=resolved.docling_ocr_enabled,
            table_structure_enabled=resolved.docling_table_structure_enabled,
        )

    candidates = select_parse_candidates(manifest)
    results: list[ParseResult] = []
    successful = partial = failed = unsupported = cache_hits = 0

    for artifact in candidates:
        source_path = extracted_dir / artifact.normalized_path
        if not source_path.is_file():
            # Path traversal protection: must remain under extracted/
            failed += 1
            results.append(
                ParseResult(
                    artifact_id=artifact.id,
                    selected_document=None,
                    attempts=[],
                    quality_decision=QualityDecision.REJECT,
                    cache_hit=False,
                )
            )
            continue

        try:
            source_path.resolve().relative_to(extracted_dir.resolve())
        except ValueError:
            failed += 1
            continue

        data = source_path.read_bytes()
        source_sha256 = artifact.sha256 or sha256_file(source_path)
        attempts: list[ParseAttempt] = []
        cache_hit = False
        selected: CanonicalDocument | None = None
        decision = QualityDecision.REJECT

        # FULL force-docling bypasses FAST pre-pass for Docling-supported formats.
        # TXT remains on the FAST/plain-text path even when force_docling is set.
        run_fast = not (
            profile_norm == "full" and force_docling and artifact.format is not ArtifactFormat.TXT
        )
        run_docling = False

        if run_fast and artifact.format in {ArtifactFormat.PDF, ArtifactFormat.TXT}:
            # Peek identity via a lightweight identity helper
            from halyk_agent.adapters.parsing.pypdf_parser import (
                plain_text_parser_identity,
                pypdf_parser_identity,
            )

            peek_identity = (
                plain_text_parser_identity(limits)
                if artifact.format is ArtifactFormat.TXT
                else pypdf_parser_identity(limits)
            )
            cached = cache.get(source_sha256=source_sha256, parser=peek_identity)
            if cached is not None and not force_docling:
                selected = cached
                cache_hit = True
                cache_hits += 1
                decision = gate.evaluate_canonical(cached, profile=profile_norm).decision
                attempts.append(_attempt_from_document(cached, duration_ms=0))
            else:
                started = time.perf_counter()
                try:
                    document = fast_parser.parse_canonical(
                        data,
                        source_file=artifact.normalized_path,
                        artifact_id=artifact.id,
                        source_sha256=source_sha256,
                        media_type=artifact.mime_type,
                    )
                    duration_ms = int((time.perf_counter() - started) * 1000)
                    attempts.append(_attempt_from_document(document, duration_ms=duration_ms))
                    evaluation = gate.evaluate_canonical(document, profile=profile_norm)
                    decision = evaluation.decision
                    selected = document
                    if document.status not in {
                        ParseStatus.FAILED,
                        ParseStatus.ENCRYPTED,
                    }:
                        cache.put(
                            document,
                            source_sha256=source_sha256,
                            parser=document.parser,
                        )
                    if profile_norm == "full" and decision is QualityDecision.FALLBACK_REQUIRED:
                        run_docling = True
                except UnsupportedDocumentFormatError as exc:
                    duration_ms = int((time.perf_counter() - started) * 1000)
                    unsupported += 1
                    results.append(
                        _failed_result(
                            artifact_id=artifact.id,
                            parser=peek_identity,
                            status=ParseStatus.UNSUPPORTED,
                            decision=QualityDecision.REJECT,
                            message=exc.message,
                            code=exc.code,
                            duration_ms=duration_ms,
                        )
                    )
                    continue
                except DocumentParsingError as exc:
                    duration_ms = int((time.perf_counter() - started) * 1000)
                    failed += 1
                    results.append(
                        _failed_result(
                            artifact_id=artifact.id,
                            parser=peek_identity,
                            status=ParseStatus.FAILED,
                            decision=QualityDecision.HUMAN_REVIEW_REQUIRED,
                            message=exc.message,
                            code=exc.code,
                            duration_ms=duration_ms,
                        )
                    )
                    continue
        elif artifact.format is ArtifactFormat.DOCX:
            if profile_norm == "fast":
                unsupported += 1
                from halyk_agent.adapters.parsing.pypdf_parser import pypdf_parser_identity

                results.append(
                    _failed_result(
                        artifact_id=artifact.id,
                        parser=pypdf_parser_identity(limits),
                        status=ParseStatus.UNSUPPORTED,
                        decision=QualityDecision.REJECT,
                        message="DOCX is not supported in FAST profile",
                        code="UNSUPPORTED_FORMAT",
                        duration_ms=0,
                    )
                )
                continue
            run_docling = True
        elif profile_norm == "full" and force_docling:
            run_docling = True
        else:
            unsupported += 1
            from halyk_agent.adapters.parsing.pypdf_parser import pypdf_parser_identity

            results.append(
                _failed_result(
                    artifact_id=artifact.id,
                    parser=pypdf_parser_identity(limits),
                    status=ParseStatus.UNSUPPORTED,
                    decision=QualityDecision.REJECT,
                    message="unsupported format for selected profile",
                    code="UNSUPPORTED_FORMAT",
                    duration_ms=0,
                )
            )
            continue

        if force_docling and artifact.format is not ArtifactFormat.TXT:
            run_docling = True

        if run_docling:
            if docling_parser is None:
                raise ParserDependencyMissingError("Docling parser required for FULL profile")
            peek = docling_parser.parser_identity()
            cached = cache.get(source_sha256=source_sha256, parser=peek)
            if cached is not None:
                selected = cached
                cache_hit = True
                cache_hits += 1
                decision = gate.evaluate_canonical(cached, profile=profile_norm).decision
                attempts.append(_attempt_from_document(cached, duration_ms=0))
            else:
                started = time.perf_counter()
                try:
                    document = docling_parser.parse_canonical(
                        data,
                        source_file=artifact.normalized_path,
                        artifact_id=artifact.id,
                        source_sha256=source_sha256,
                        media_type=artifact.mime_type,
                    )
                    duration_ms = int((time.perf_counter() - started) * 1000)
                    attempts.append(_attempt_from_document(document, duration_ms=duration_ms))
                    evaluation = gate.evaluate_canonical(document, profile=profile_norm)
                    decision = evaluation.decision
                    selected = document
                    cache.put(
                        document,
                        source_sha256=source_sha256,
                        parser=document.parser,
                    )
                except ParserDependencyMissingError:
                    raise
                except DocumentParsingError as exc:
                    duration_ms = int((time.perf_counter() - started) * 1000)
                    attempts.append(
                        ParseAttempt(
                            parser=peek,
                            status=ParseStatus.FAILED,
                            metrics=empty_metrics(),
                            warnings=[],
                            error_code=exc.code,
                            error_message=exc.message,
                            duration_ms=duration_ms,
                        )
                    )
                    decision = QualityDecision.HUMAN_REVIEW_REQUIRED

        if selected is not None and selected.status is ParseStatus.SUCCESS:
            successful += 1
        elif selected is not None and selected.status is ParseStatus.PARTIAL:
            partial += 1
        elif selected is not None and selected.status is ParseStatus.UNSUPPORTED:
            unsupported += 1
        elif selected is None or selected.status in {
            ParseStatus.FAILED,
            ParseStatus.ENCRYPTED,
            ParseStatus.QUALITY_REJECTED,
        }:
            failed += 1

        if selected is not None and selected.status not in {
            ParseStatus.FAILED,
            ParseStatus.ENCRYPTED,
        }:
            _atomic_write_text(
                documents_dir / f"{artifact.id}.json",
                _dump_json(selected),
            )

        results.append(
            ParseResult(
                artifact_id=artifact.id,
                selected_document=selected,
                attempts=attempts,
                quality_decision=decision,
                cache_hit=cache_hit,
            )
        )

    report = ParseBatchReport(
        profile=profile_norm,
        total_candidates=len(candidates),
        successful=successful,
        partial=partial,
        failed=failed,
        unsupported=unsupported,
        cache_hits=cache_hits,
        results=results,
    )

    # Evidence catalog JSONL
    evidence_path = output_dir / "evidence_catalog.jsonl"
    lines: list[str] = []
    for result in sorted(results, key=lambda item: item.artifact_id):
        selected_document = result.selected_document
        if selected_document is None or selected_document.status in {
            ParseStatus.FAILED,
            ParseStatus.ENCRYPTED,
        }:
            continue
        for span in build_evidence_catalog(selected_document):
            lines.append(
                json.dumps(span.model_dump(mode="json"), ensure_ascii=False, allow_nan=False)
            )
    _atomic_write_text(evidence_path, "\n".join(lines) + ("\n" if lines else ""))

    _atomic_write_text(output_dir / "parse_report.json", _dump_json(report))

    summary_lines = [
        "# Parsing summary",
        "",
        f"- Profile: `{profile_norm}`",
        f"- Candidates: {report.total_candidates}",
        f"- Successful: {report.successful}",
        f"- Partial: {report.partial}",
        f"- Failed: {report.failed}",
        f"- Unsupported: {report.unsupported}",
        f"- Cache hits: {report.cache_hits}",
        "",
        "## Results",
        "",
    ]
    for result in report.results:
        status = (
            result.selected_document.status.value
            if result.selected_document is not None
            else "NONE"
        )
        summary_lines.append(
            f"- `{result.artifact_id}` — {status} / {result.quality_decision.value}"
            f"{' (cache)' if result.cache_hit else ''}"
        )
    summary_lines.append("")
    _atomic_write_text(output_dir / "parsing_summary.md", "\n".join(summary_lines))
    return report
