"""OCR quality facade — re-exports domain page-quality helpers used by diagnostics."""

from __future__ import annotations

from halyk_agent.domain.page_quality import (
    PageQualityState,
    PageSignals,
    classify_signals,
    diagnose_canonical_page,
    is_blocking_page_quality,
)

__all__ = [
    "PageQualityState",
    "PageSignals",
    "classify_signals",
    "diagnose_canonical_page",
    "is_blocking_page_quality",
]
