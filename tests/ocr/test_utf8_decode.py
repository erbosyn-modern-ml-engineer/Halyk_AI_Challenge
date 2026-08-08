"""OCR UTF-8 decode contract — never locale/cp1251 mojibake."""

# ruff: noqa: RUF001

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from halyk_agent.adapters.ocr.tesseract_cli import _run_tesseract
from halyk_agent.domain.ocr import OcrBackendIdentity, OcrBackendKind, ocr_cache_identity


def test_tesseract_stdout_utf8_cyrillic_survives() -> None:
    sample = "Примечание 8 — Корректировки EBITDA\nТаға Holding\n"
    utf8_bytes = sample.encode("utf-8")
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = utf8_bytes
    proc.stderr = b""
    with patch("subprocess.run", return_value=proc) as run:
        text, _ = _run_tesseract(
            executable="tesseract",
            image_path=Path("page.png"),
            languages=["eng", "rus", "kaz"],
            psm=6,
            timeout=10.0,
        )
    assert run.call_args.kwargs.get("text") is False
    assert "encoding" not in run.call_args.kwargs
    assert text == sample
    assert "РџСЂРёРјРµС‡Р°РЅРёРµ" not in text


def test_tesseract_stdout_invalid_utf8_fails_closed() -> None:
    proc = MagicMock()
    proc.returncode = 0
    # Invalid UTF-8 sequence.
    proc.stdout = b"\xff\xfe invalid"
    proc.stderr = b""
    with (
        patch("subprocess.run", return_value=proc),
        pytest.raises(RuntimeError, match="OCR_UTF8_DECODE_FAILED"),
    ):
        _run_tesseract(
            executable="tesseract",
            image_path=Path("page.png"),
            languages=["eng", "rus", "kaz"],
            psm=6,
            timeout=10.0,
        )


def test_ocr_cache_identity_v2_invalidates_mojibake_cache() -> None:
    from halyk_agent.adapters.ocr.cache import OCR_CACHE_SCHEMA
    from halyk_agent.domain.ids import deterministic_id

    backend = OcrBackendIdentity(
        kind=OcrBackendKind.TESSERACT_CLI,
        backend_version="tesseract v5",
        executable_or_package="tesseract",
        language_data_identity="abc",
        languages=["eng", "rus", "kaz"],
        render_scale=2.0,
        page_segmentation_mode=6,
        configuration_hash="cfg",
    )
    identity_v2 = ocr_cache_identity(source_sha256="a" * 64, page_number=2, backend=backend)
    identity_v1 = deterministic_id(
        "halyk.ocr_cache.v1",
        "a" * 64,
        2,
        backend.kind.value,
        backend.backend_version,
        backend.language_data_identity,
        "+".join(sorted(backend.languages)),
        f"{backend.render_scale:.4f}",
        backend.page_segmentation_mode,
        backend.configuration_hash,
    )
    assert identity_v2 != identity_v1
    assert OCR_CACHE_SCHEMA.endswith(".v2")
