"""Parallel FAST parsing for the fixed competition pipeline.

Stage 3's general batch service is intentionally conservative and sequential.
The competition corpus is a large set of independent PDFs, so Stage 7 executes
that same pypdf/finalization/quality contract in bounded worker processes and
merges results deterministically.
"""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from halyk_agent.adapters.parsing.finalize import finalize_canonical_parse
from halyk_agent.adapters.parsing.limits import ParserLimits
from halyk_agent.adapters.parsing.pypdf_parser import PyPdfDocumentParser, pypdf_parser_identity
from halyk_agent.adapters.parsing.quality import DeterministicParseQualityGate, QualityThresholds
from halyk_agent.config import Settings
from halyk_agent.domain.datasets import DatasetArtifact, DatasetManifest
from halyk_agent.domain.evidence_factory import build_evidence_catalog
from halyk_agent.domain.parsing import (
    ParseAttempt,
    ParseBatchReport,
    ParseResult,
    ParseStatus,
    ParseWarning,
    ParseWarningCode,
    QualityDecision,
    empty_metrics,
)


def _limits(settings: dict[str, Any]) -> ParserLimits:
    return ParserLimits(
        max_pdf_pages=int(settings["max_pdf_pages"]),
        max_page_characters=int(settings["max_page_characters"]),
        max_document_characters=int(settings["max_document_characters"]),
        max_parser_warnings=int(settings["max_parser_warnings"]),
    )


def _thresholds(settings: dict[str, Any]) -> QualityThresholds:
    return QualityThresholds(
        min_total_characters=int(settings["quality_min_total_characters"]),
        max_empty_page_ratio=float(settings["quality_max_empty_page_ratio"]),
        max_replacement_character_ratio=float(settings["quality_max_replacement_character_ratio"]),
        min_alphanumeric_character_ratio=float(settings["quality_min_alphanumeric_ratio"]),
        max_control_character_count=int(settings["quality_max_control_character_count"]),
        max_duplicate_line_ratio=float(settings["quality_max_duplicate_line_ratio"]),
        max_pages_without_text_ratio=float(settings["quality_max_pages_without_text_ratio"]),
    )


def _parse_one(
    artifact_payload: dict[str, Any],
    source_path: str,
    settings_payload: dict[str, Any],
) -> dict[str, Any]:
    artifact = DatasetArtifact.model_validate(artifact_payload)
    limits = _limits(settings_payload)
    parser = PyPdfDocumentParser(limits=limits)
    gate = DeterministicParseQualityGate(_thresholds(settings_payload))
    started = time.perf_counter()
    try:
        data = Path(source_path).read_bytes()
        candidate, visuals = parser.parse_with_visuals(
            data,
            source_file=artifact.normalized_path,
            artifact_id=artifact.id,
            source_sha256=artifact.sha256,
            media_type=artifact.mime_type,
        )
        finalized = finalize_canonical_parse(candidate, page_visuals=visuals)
        document = finalized.document
        decision = gate.evaluate_canonical(document, profile="fast").decision
        duration_ms = int((time.perf_counter() - started) * 1000)
        attempt = ParseAttempt(
            parser=document.parser,
            status=document.status,
            metrics=document.metrics,
            warnings=list(document.warnings),
            duration_ms=duration_ms,
        )
        result = ParseResult(
            artifact_id=artifact.id,
            selected_document=document,
            attempts=[attempt],
            quality_decision=decision,
            cache_hit=False,
        )
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        identity = pypdf_parser_identity(limits)
        attempt = ParseAttempt(
            parser=identity,
            status=ParseStatus.FAILED,
            metrics=empty_metrics(),
            warnings=[
                ParseWarning(
                    code=ParseWarningCode.PARSER_ERROR,
                    message=f"parallel FAST parse failed: {exc.__class__.__name__}",
                )
            ],
            error_code="PARALLEL_FAST_PARSE",
            error_message=f"{exc.__class__.__name__}: {exc}",
            duration_ms=duration_ms,
        )
        result = ParseResult(
            artifact_id=artifact.id,
            selected_document=None,
            attempts=[attempt],
            quality_decision=QualityDecision.HUMAN_REVIEW_REQUIRED,
            cache_hit=False,
        )
    return result.model_dump(mode="json")


def _settings_payload(settings: Settings) -> dict[str, Any]:
    names = (
        "max_pdf_pages",
        "max_page_characters",
        "max_document_characters",
        "max_parser_warnings",
        "quality_min_total_characters",
        "quality_max_empty_page_ratio",
        "quality_max_replacement_character_ratio",
        "quality_min_alphanumeric_ratio",
        "quality_max_control_character_count",
        "quality_max_duplicate_line_ratio",
        "quality_max_pages_without_text_ratio",
    )
    return {name: getattr(settings, name) for name in names}


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def parse_competition_documents_parallel(
    inspection_dir: Path,
    output_dir: Path,
    *,
    settings: Settings,
    max_workers: int | None = None,
) -> ParseBatchReport:
    """Parse sanitized PDF artifacts in bounded processes and merge deterministically."""

    inspection_dir = inspection_dir.resolve()
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"parallel parse output is not empty: {output_dir}")
    manifest_path = inspection_dir / "manifest.json"
    extracted_dir = inspection_dir / "extracted"
    manifest = DatasetManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    artifacts = sorted(
        (artifact for artifact in manifest.artifacts if artifact.role.value == "DOCUMENT"),
        key=lambda item: item.id,
    )
    if not artifacts:
        raise ValueError("inspection manifest contains no document artifacts")

    output_dir.mkdir(parents=True, exist_ok=True)
    documents_dir = output_dir / "documents"
    documents_dir.mkdir(parents=True, exist_ok=True)
    workers = max_workers or min(4, max(1, (os.cpu_count() or 2) - 1))
    payload = _settings_payload(settings)
    tasks = [
        (
            artifact.model_dump(mode="json"),
            str((extracted_dir / artifact.normalized_path).resolve()),
            payload,
        )
        for artifact in artifacts
    ]
    with ProcessPoolExecutor(max_workers=workers) as executor:
        raw_results = list(executor.map(_parse_one_star, tasks, chunksize=4))
    results = [ParseResult.model_validate(item) for item in raw_results]
    results.sort(key=lambda item: item.artifact_id)

    successful = sum(
        1
        for item in results
        if item.selected_document is not None
        and item.selected_document.status is ParseStatus.SUCCESS
    )
    partial = sum(
        1
        for item in results
        if item.selected_document is not None
        and item.selected_document.status is ParseStatus.PARTIAL
    )
    unsupported = sum(
        1
        for item in results
        if item.selected_document is not None
        and item.selected_document.status is ParseStatus.UNSUPPORTED
    )
    failed = len(results) - successful - partial - unsupported
    report = ParseBatchReport(
        profile="fast",
        total_candidates=len(results),
        successful=successful,
        partial=partial,
        failed=failed,
        unsupported=unsupported,
        cache_hits=0,
        results=results,
    )

    evidence_lines: list[str] = []
    for result in results:
        document = result.selected_document
        if document is None or document.status in {ParseStatus.FAILED, ParseStatus.ENCRYPTED}:
            continue
        _write(
            documents_dir / f"{result.artifact_id}.json",
            document.model_dump_json(indent=2) + "\n",
        )
        for span in build_evidence_catalog(document):
            evidence_lines.append(
                json.dumps(span.model_dump(mode="json"), ensure_ascii=False, allow_nan=False)
            )
    _write(
        output_dir / "evidence_catalog.jsonl",
        "\n".join(evidence_lines) + ("\n" if evidence_lines else ""),
    )
    _write(output_dir / "parse_report.json", report.model_dump_json(indent=2) + "\n")
    summary = [
        "# Parsing summary",
        "",
        "- Profile: `fast-parallel`",
        f"- Workers: {workers}",
        f"- Candidates: {report.total_candidates}",
        f"- Successful: {report.successful}",
        f"- Partial: {report.partial}",
        f"- Failed: {report.failed}",
        f"- Unsupported: {report.unsupported}",
        "",
    ]
    _write(output_dir / "parsing_summary.md", "\n".join(summary))
    return report


def _parse_one_star(args: tuple[dict[str, Any], str, dict[str, Any]]) -> dict[str, Any]:
    return _parse_one(*args)
