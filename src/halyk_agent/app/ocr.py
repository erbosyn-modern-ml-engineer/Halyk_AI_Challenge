"""Selective provenance-safe OCR application service (Stage 5A.4)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from halyk_agent.adapters.ocr.cache import LocalOcrPageCache
from halyk_agent.adapters.ocr.merge import merge_ocr_into_document
from halyk_agent.adapters.ocr.planner import plan_selective_ocr
from halyk_agent.adapters.ocr.probe import probe_ocr_environment
from halyk_agent.adapters.ocr.protocol import OcrBackend
from halyk_agent.adapters.ocr.tesseract_cli import TesseractCliOcrBackend
from halyk_agent.adapters.parsing.post_parse_gate import apply_post_parse_quality_gate
from halyk_agent.domain.evidence_factory import build_evidence_catalog
from halyk_agent.domain.ocr import (
    DEFAULT_MAX_SELECTED_PAGES,
    REQUIRED_OCR_LANGUAGES,
    OcrBackendKind,
    OcrPageRequest,
    OcrPageResult,
    OcrPageStatus,
    OcrProbeReport,
    OcrRunReport,
)
from halyk_agent.domain.page_quality import is_blocking_page_quality
from halyk_agent.domain.parsing import CanonicalDocument, ParseBatchReport, ParseStatus


class SelectiveOcrError(Exception):
    """Selective OCR service error."""

    def __init__(self, message: str, *, code: str = "OCR_ERROR") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(path)


def _dump(model: Any) -> str:
    if hasattr(model, "model_dump_json"):
        payload = model.model_dump_json(indent=2)
        return str(payload) + "\n"
    return json.dumps(model, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def load_parsed_documents(parsed_dir: Path) -> tuple[ParseBatchReport, list[CanonicalDocument]]:
    """Load Stage 5A parse outputs."""
    report_path = parsed_dir / "parse_report.json"
    if not report_path.is_file():
        raise SelectiveOcrError("parse_report.json missing", code="MISSING_PARSE_REPORT")
    report = ParseBatchReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    docs: list[CanonicalDocument] = []
    documents_dir = parsed_dir / "documents"
    if documents_dir.is_dir():
        for path in sorted(documents_dir.glob("*.json")):
            docs.append(CanonicalDocument.model_validate_json(path.read_text(encoding="utf-8")))
    # Also include selected_document from FAILED results in the report (not on disk).
    seen = {doc.artifact_id for doc in docs}
    for result in report.results:
        selected = result.selected_document
        if selected is not None and selected.artifact_id not in seen:
            docs.append(selected)
            seen.add(selected.artifact_id)
    return report, docs


def select_backend(
    *,
    backend_name: str | None,
    languages: list[str],
    scale: float,
    psm: int,
    timeout: float,
    injected: OcrBackend | None = None,
) -> tuple[OcrBackend | None, OcrProbeReport]:
    """Select an explicit backend. No silent fallback."""
    probe = probe_ocr_environment()
    if injected is not None:
        return injected, probe
    name = (backend_name or "").strip().upper() or None
    if name in {None, "", "AUTO"}:
        # Competition default: only Tesseract when offline-ready; else none.
        if probe.selected_kind is OcrBackendKind.TESSERACT_CLI:
            return (
                TesseractCliOcrBackend(
                    languages=languages,
                    render_scale=scale,
                    page_segmentation_mode=psm,
                    timeout_seconds=timeout,
                ),
                probe,
            )
        return None, probe
    if name in {"TESSERACT", "TESSERACT_CLI"}:
        return (
            TesseractCliOcrBackend(
                languages=languages,
                render_scale=scale,
                page_segmentation_mode=psm,
                timeout_seconds=timeout,
            ),
            probe,
        )
    raise SelectiveOcrError(
        f"unsupported explicit backend {backend_name!r}; no silent fallback",
        code="UNSUPPORTED_BACKEND",
    )


async def run_selective_ocr(
    parsed_dir: Path,
    output_dir: Path,
    *,
    overwrite: bool = False,
    backend_name: str | None = None,
    languages: list[str] | None = None,
    only_required: bool = True,
    max_pages: int = DEFAULT_MAX_SELECTED_PAGES,
    timeout: float = 60.0,
    scale: float = 2.0,
    psm: int = 6,
    source_roots: list[Path] | None = None,
    backend: OcrBackend | None = None,
) -> OcrRunReport:
    """Run selective OCR over blocking pages from a parse output directory."""
    parsed_dir = parsed_dir.resolve()
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()) and not overwrite:
        raise SelectiveOcrError(
            "output directory is not empty; pass --overwrite",
            code="OUTPUT_EXISTS",
        )
    output_dir.mkdir(parents=True, exist_ok=True)

    langs = list(languages or REQUIRED_OCR_LANGUAGES)
    report, documents = load_parsed_documents(parsed_dir)
    source_paths = _resolve_source_paths(documents, source_roots or [])

    # Count pages/pdfs from loaded docs for the plan summary.
    total_pages = sum(len(doc.pages) for doc in documents)
    total_pdfs = sum(1 for doc in documents if doc.source_file.lower().endswith(".pdf"))

    plan = plan_selective_ocr(
        documents,
        source_paths=source_paths,
        only_required=only_required,
        max_pages=max_pages,
        total_pdfs=total_pdfs,
        total_pages=total_pages,
    )
    _atomic_write(output_dir / "ocr_plan.json", _dump(plan))

    selected_backend, probe = select_backend(
        backend_name=backend_name,
        languages=langs,
        scale=scale,
        psm=psm,
        timeout=timeout,
        injected=backend,
    )
    _atomic_write(output_dir / "ocr_probe.json", _dump(probe))

    if selected_backend is None:
        run = OcrRunReport(
            backend=None,
            probe=probe,
            plan=plan,
            selected_pages=len(plan.selections),
            attempted_pages=0,
            succeeded_pages=0,
            failed_pages=0,
            remaining_blocking_pages=plan.blocking_pages,
            offline_ready=False,
            blocked_reason=(
                "No offline-ready OCR backend. Install Tesseract CLI with "
                "eng+rus+kaz tessdata, or provide an approved offline backend."
            ),
            documents_processed=0,
        )
        _write_run_outputs(output_dir, run, documents=[], report=report)
        raise SelectiveOcrError(
            run.blocked_reason or "OCR backend unavailable", code="OCR_BACKEND_UNAVAILABLE"
        )

    availability = await selected_backend.probe()
    if not availability.offline_ready:
        run = OcrRunReport(
            backend=None,
            probe=probe,
            plan=plan,
            selected_pages=len(plan.selections),
            attempted_pages=0,
            succeeded_pages=0,
            failed_pages=0,
            remaining_blocking_pages=plan.blocking_pages,
            offline_ready=False,
            blocked_reason="Selected backend probe reports not offline-ready",
        )
        _write_run_outputs(output_dir, run, documents=[], report=report)
        raise SelectiveOcrError(
            run.blocked_reason or "OCR backend unavailable",
            code="OCR_BACKEND_UNAVAILABLE",
        )

    identity = None
    if isinstance(selected_backend, TesseractCliOcrBackend):
        identity = selected_backend.identity(availability)
    elif hasattr(selected_backend, "identity"):
        identity = selected_backend.identity()

    cache = LocalOcrPageCache(output_dir / "ocr_cache")
    requests: list[OcrPageRequest] = []
    cached_results: list[OcrPageResult] = []
    for selection in plan.selections:
        req = OcrPageRequest(
            source_path=selection.source_path,
            source_sha256=selection.source_sha256,
            document_id=selection.document_id,
            document_version_id=selection.document_version_id,
            page_number=selection.page_number,
            reason=selection.reason,
            page_quality_state=selection.page_quality_state,
            languages=langs,
        )
        if identity is not None:
            hit = cache.get(
                source_sha256=req.source_sha256,
                page_number=req.page_number,
                backend=identity,
            )
            if hit is not None:
                cached_results.append(hit)
                continue
        requests.append(req)

    live_results = list(await selected_backend.recognize_pages(requests)) if requests else []
    if identity is not None:
        for item in live_results:
            cache.put(item, backend=identity)

    all_results = cached_results + live_results
    # Preserve plan order.
    order = {(s.source_sha256, s.page_number): i for i, s in enumerate(plan.selections)}
    all_results.sort(
        key=lambda item: order.get((item.request.source_sha256, item.request.page_number), 10**9)
    )

    by_doc: dict[str, list[OcrPageResult]] = {}
    for item in all_results:
        by_doc.setdefault(item.request.document_id, []).append(item)

    enriched: list[CanonicalDocument] = []
    remaining_blocking = 0
    for document in documents:
        page_results = by_doc.get(document.document_id, [])
        if page_results:
            merged, rem = merge_ocr_into_document(document, page_results)
            enriched.append(merged)
            remaining_blocking += rem
        else:
            gated = apply_post_parse_quality_gate(document)
            enriched.append(gated.document)
            remaining_blocking += sum(
                1 for state in gated.summary.page_states if is_blocking_page_quality(state)
            )

    succeeded = sum(1 for r in all_results if r.status is OcrPageStatus.OCR_SUCCEEDED)
    failed = len(all_results) - succeeded
    run = OcrRunReport(
        backend=identity,
        probe=probe,
        plan=plan,
        selected_pages=len(plan.selections),
        attempted_pages=len(requests),
        succeeded_pages=succeeded,
        failed_pages=failed,
        remaining_blocking_pages=remaining_blocking,
        cache_hits=cache.hits,
        cache_misses=cache.misses,
        temporary_bytes_written=sum(r.temporary_bytes_written for r in live_results),
        persistent_cache_bytes_written=cache.bytes_written,
        cleanup_failures=sum(1 for r in live_results if not r.temporary_cleanup_ok),
        page_results=all_results,
        documents_processed=len(enriched),
        offline_ready=True,
        blocked_reason=None,
    )
    _write_run_outputs(output_dir, run, documents=enriched, report=report)
    return run


def _resolve_source_paths(
    documents: list[CanonicalDocument],
    source_roots: list[Path],
) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for document in documents:
        name = Path(document.source_file).name
        resolved: Path | None = None
        for root in source_roots:
            candidate = root / name
            if candidate.is_file():
                resolved = candidate.resolve()
                break
        if resolved is not None:
            mapping[document.artifact_id] = str(resolved)
            mapping[document.id] = str(resolved)
    return mapping


def _write_run_outputs(
    output_dir: Path,
    run: OcrRunReport,
    *,
    documents: list[CanonicalDocument],
    report: ParseBatchReport,
) -> None:
    _atomic_write(output_dir / "ocr_report.json", _dump(run))
    docs_dir = output_dir / "documents"
    docs_dir.mkdir(parents=True, exist_ok=True)
    evidence_lines: list[str] = []
    successful = partial = failed = unsupported = 0
    for document in documents:
        if document.status is ParseStatus.SUCCESS:
            successful += 1
        elif document.status is ParseStatus.PARTIAL:
            partial += 1
        elif document.status is ParseStatus.UNSUPPORTED:
            unsupported += 1
        else:
            failed += 1
        if document.status not in {ParseStatus.FAILED, ParseStatus.ENCRYPTED}:
            _atomic_write(docs_dir / f"{document.artifact_id}.json", _dump(document))
            for span in build_evidence_catalog(document):
                # Attach OCR origin via metadata-bearing spans already exact.
                payload = span.model_dump(mode="json")
                # Best-effort: mark OCR spans when block metadata says so.
                evidence_lines.append(json.dumps(payload, ensure_ascii=False))
    out_report = report.model_copy(
        update={
            "successful": successful,
            "partial": partial,
            "failed": failed,
            "unsupported": unsupported,
            "results": report.results,
        }
    )
    _atomic_write(output_dir / "parse_report.json", _dump(out_report))
    _atomic_write(
        output_dir / "evidence_catalog.jsonl",
        ("\n".join(evidence_lines) + ("\n" if evidence_lines else "")),
    )
    summary = "\n".join(
        [
            "# Selective OCR summary",
            "",
            f"- Offline ready: {run.offline_ready}",
            f"- Selected pages: {run.selected_pages}",
            f"- Attempted (backend calls): {run.attempted_pages}",
            f"- Succeeded: {run.succeeded_pages}",
            f"- Failed: {run.failed_pages}",
            f"- Remaining blocking: {run.remaining_blocking_pages}",
            f"- Cache hits/misses: {run.cache_hits}/{run.cache_misses}",
            f"- Temporary bytes: {run.temporary_bytes_written}",
            f"- Persistent cache bytes: {run.persistent_cache_bytes_written}",
            f"- Cleanup failures: {run.cleanup_failures}",
            f"- Blocked reason: {run.blocked_reason or 'none'}",
            "",
        ]
    )
    _atomic_write(output_dir / "ocr_summary.md", summary)


def run_ocr_probe(*, json_output: bool = False) -> tuple[OcrProbeReport, str]:
    """CLI helper for `ocr probe`."""
    report = probe_ocr_environment()
    if json_output:
        return report, report.model_dump_json(indent=2) + "\n"
    lines = [
        f"selected_kind={report.selected_kind.value}",
        f"offline_ready={report.offline_ready_backend}",
        f"downloads_performed={report.downloads_performed}",
        f"docling_version={report.docling_version}",
    ]
    for candidate in report.candidates:
        lines.append(
            f"candidate={candidate.kind.value} installed={candidate.installed} "
            f"offline_ready={candidate.offline_ready} missing={candidate.missing_components} "
            f"missing_langs={candidate.missing_languages}"
        )
    return report, "\n".join(lines) + "\n"
