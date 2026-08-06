"""OCR quality gate and bounded diagnostics."""

from halyk_agent.solver.ocr.diagnostics import run_ocr_diagnostic
from halyk_agent.solver.ocr.states import PageQualityState

__all__ = ["PageQualityState", "run_ocr_diagnostic"]
