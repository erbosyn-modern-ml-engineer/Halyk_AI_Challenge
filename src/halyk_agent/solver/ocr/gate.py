"""OCR quality gate facade over domain page-quality signals."""

from __future__ import annotations

from halyk_agent.domain.page_quality import (
    PageQualityState,
    PageSignals,
    classify_signals,
    diagnose_canonical_page,
    trusted_success_blocked,
)

__all__ = [
    "PageQualityState",
    "PageSignals",
    "classify_signals",
    "diagnose_canonical_page",
    "trusted_success_blocked",
]
