"""PDFium calls must be serialized even when OCR subprocesses are concurrent."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from halyk_agent.adapters.ocr import tesseract_cli
from halyk_agent.adapters.ocr.tesseract_cli import TesseractCliOcrBackend
from halyk_agent.domain.ocr import OcrBackendAvailability, OcrBackendKind, OcrPageRequest
from halyk_agent.domain.page_quality import PageQualityState


@pytest.mark.asyncio
async def test_pdfium_render_calls_are_serialized(monkeypatch: pytest.MonkeyPatch) -> None:
    active = 0
    max_active = 0
    state_lock = threading.Lock()

    def fake_render(*_args: object, **_kwargs: object) -> bytes:
        nonlocal active, max_active
        with state_lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.03)
        with state_lock:
            active -= 1
        return b"synthetic-png"

    def fake_tesseract(**_kwargs: object) -> tuple[str, float | None]:
        time.sleep(0.03)
        return ("Recovered covenant text with enough alphanumeric content. " * 5, None)

    monkeypatch.setattr(tesseract_cli, "_render_page_png", fake_render)
    monkeypatch.setattr(tesseract_cli, "_run_tesseract", fake_tesseract)

    backend = TesseractCliOcrBackend(max_concurrency=2)
    availability = OcrBackendAvailability(
        kind=OcrBackendKind.TESSERACT_CLI,
        installed=True,
        offline_ready=True,
        version="test",
        executable_path="tesseract",
        language_data_path="/tmp/tessdata",
        installed_languages=["eng", "rus", "kaz"],
        required_languages=["eng", "rus", "kaz"],
    )

    async def fake_probe() -> OcrBackendAvailability:
        return availability

    monkeypatch.setattr(backend, "probe", fake_probe)
    requests = [
        OcrPageRequest(
            source_path=f"doc-{page}.pdf",
            source_sha256=str(page) * 64,
            document_id=f"doc-{page}",
            document_version_id=f"version-{page}",
            page_number=1,
            reason="blocking:OCR_REQUIRED",
            page_quality_state=PageQualityState.OCR_REQUIRED,
            languages=["eng", "rus", "kaz"],
        )
        for page in (1, 2)
    ]

    results = await asyncio.wait_for(backend.recognize_pages(requests), timeout=2.0)

    assert len(results) == 2
    assert max_active == 1
