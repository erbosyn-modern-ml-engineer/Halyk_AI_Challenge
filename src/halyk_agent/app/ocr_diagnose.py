"""OCR diagnostic application wrapper."""

from __future__ import annotations

from pathlib import Path

from halyk_agent.solver.ocr.diagnostics import run_ocr_diagnostic


def run_ocr_diagnose(documents: Path, output: Path) -> dict[str, object]:
    return run_ocr_diagnostic(documents, output)
