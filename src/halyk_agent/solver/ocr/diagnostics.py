"""Bounded OCR diagnostic over public PDFs (no mass OCR)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from halyk_agent.domain.page_quality import (
    PageQualityState,
    PageSignals,
    classify_signals,
    page_image_count_from_pypdf,
)
from halyk_agent.solver.failures import FailureEvent, FailureMode


def probe_ocr_backend() -> dict[str, Any]:
    """Probe already-installed OCR capability without downloads."""
    # Prefer Docling if importable; do not download resources.
    try:
        import docling  # noqa: F401
    except ImportError:
        return {
            "available": False,
            "backend": None,
            "reason": "docling_not_installed",
            "estimated_install": "optional extra `full` (docling==2.118.0); may pull large models",
        }
    # Docling may still need model weights; treat OCR as unavailable unless explicit env enables it.
    if os.environ.get("HALYK_DOCLING_OCR_ENABLED", "").strip().lower() not in {"1", "true", "yes"}:
        return {
            "available": False,
            "backend": "docling",
            "reason": "docling_present_but_ocr_not_enabled_or_weights_unverified",
            "estimated_install": (
                "Docling OCR models may need multi-hundred MB downloads; requires explicit approval"
            ),
        }
    return {
        "available": False,
        "backend": "docling",
        "reason": "ocr_weights_not_preverified_offline",
        "estimated_install": "unknown without network probe; do not download automatically",
    }


def diagnose_pdf(path: Path) -> list[dict[str, Any]]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages: list[dict[str, Any]] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        image_count = page_image_count_from_pypdf(page)
        chars = len(text)
        alnum = (sum(1 for ch in text if ch.isalnum()) / chars) if chars else 0.0
        repl = (text.count("\ufffd") / chars) if chars else 0.0
        heading = False
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if lines and len(lines) <= 3 and sum(len(ln) for ln in lines) < 80:
            heading = True
        signals = PageSignals(
            char_count=chars,
            alphanumeric_ratio=alnum,
            replacement_ratio=repl,
            image_count=image_count,
            heading_without_body=heading,
            empty_table_near_heading=False,
            parser_status=None,
        )
        state = classify_signals(signals)
        pages.append(
            {
                "page_number": index,
                "state": state.value,
                "char_count": chars,
                "alphanumeric_ratio": alnum,
                "replacement_ratio": repl,
                "image_count": image_count,
                "heading_without_body": heading,
            }
        )
    return pages


def run_ocr_diagnostic(documents_dir: Path, output_dir: Path) -> dict[str, Any]:
    documents_dir = documents_dir.resolve()
    pdfs = sorted(documents_dir.glob("*.pdf"))
    backend = probe_ocr_backend()
    failures: list[FailureEvent] = []
    file_reports: list[dict[str, Any]] = []
    ocr_required_pages = 0
    total_pages = 0

    for pdf in pdfs:
        pages = diagnose_pdf(pdf)
        total_pages += len(pages)
        required = [p for p in pages if p["state"] == PageQualityState.OCR_REQUIRED.value]
        ocr_required_pages += len(required)
        file_reports.append(
            {
                "source_file": pdf.name,
                "page_count": len(pages),
                "ocr_required_pages": [p["page_number"] for p in required],
                "pages": pages,
            }
        )
        for page in required:
            failures.append(
                FailureEvent(
                    event_id=f"ocr-{pdf.name}-{page['page_number']}",
                    run_id="ocr-diagnostic",
                    stage="ocr_quality_gate",
                    interaction_edge="pdf->text_extract",
                    fault_side="parser",
                    failure_mode=FailureMode.OCR_REQUIRED,
                    observed_symptom=(
                        f"page={page['page_number']} chars={page['char_count']} "
                        f"images={page['image_count']}"
                    ),
                    evidence_refs=[pdf.name],
                    recommended_repair_owner="ocr_backend",
                )
            )

    if ocr_required_pages and not backend.get("available"):
        failures.append(
            FailureEvent(
                event_id="ocr-backend-unavailable",
                run_id="ocr-diagnostic",
                stage="ocr_quality_gate",
                interaction_edge="ocr_backend->pages",
                fault_side="environment",
                failure_mode=FailureMode.OCR_BACKEND_UNAVAILABLE,
                observed_symptom=str(backend.get("reason")),
                earliest_unrecovered_event_id=failures[0].event_id if failures else None,
                evidence_refs=[],
                recommended_repair_owner="operator",
            )
        )

    report = {
        "documents_dir": str(documents_dir.as_posix()),
        "pdf_count": len(pdfs),
        "page_count": total_pages,
        "ocr_required_page_count": ocr_required_pages,
        "backend": backend,
        "files": file_reports,
        "note": "Production detector is filename-agnostic; diagnostic may list source files.",
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "ocr_diagnostic.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    summary = "\n".join(
        [
            "# OCR diagnostic summary",
            "",
            f"- PDFs scanned: {len(pdfs)}",
            f"- pages scanned: {total_pages}",
            f"- OCR_REQUIRED pages: {ocr_required_pages}",
            f"- backend available: {backend.get('available')}",
            f"- backend reason: {backend.get('reason')}",
            "",
            "No OCR weights were downloaded. No original PDFs were modified.",
            "",
        ]
    )
    (output_dir / "ocr_summary.md").write_text(summary, encoding="utf-8", newline="\n")
    (output_dir / "failure_events.jsonl").write_text(
        "\n".join(
            json.dumps(e.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
            for e in failures
        )
        + ("\n" if failures else ""),
        encoding="utf-8",
        newline="\n",
    )
    return report
