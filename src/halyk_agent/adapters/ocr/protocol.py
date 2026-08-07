"""Narrow async OCR backend Protocol."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from halyk_agent.domain.ocr import OcrBackendAvailability, OcrPageRequest, OcrPageResult


@runtime_checkable
class OcrBackend(Protocol):
    """Explicit OCR backend — no silent engine fallback."""

    async def probe(self) -> OcrBackendAvailability:
        """Return offline readiness without downloading models."""
        ...

    async def recognize_pages(
        self,
        requests: Sequence[OcrPageRequest],
    ) -> Sequence[OcrPageResult]:
        """OCR only the given pages, preserving request order."""
        ...
