"""Read-only OCR environment probe (no downloads, no model init)."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from halyk_agent.domain.ocr import (
    REQUIRED_OCR_LANGUAGES,
    OcrBackendAvailability,
    OcrBackendKind,
    OcrProbeReport,
)


def _pkg_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def _find_spec(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _run_bounded(cmd: list[str], *, timeout: float = 5.0) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "TESSDATA_PREFIX": os.environ.get("TESSDATA_PREFIX", "")},
        )
        stdout = (proc.stdout or "")[:8000]
        stderr = (proc.stderr or "")[:8000]
        return proc.returncode, stdout, stderr
    except FileNotFoundError:
        return 127, "", "not_found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def probe_tesseract_cli() -> OcrBackendAvailability:
    """Probe Tesseract CLI without OCR work or downloads."""
    path = shutil.which("tesseract")
    missing: list[str] = []
    notes: list[str] = []
    if not path:
        return OcrBackendAvailability(
            kind=OcrBackendKind.TESSERACT_CLI,
            installed=False,
            offline_ready=False,
            missing_components=["tesseract_executable"],
            missing_languages=list(REQUIRED_OCR_LANGUAGES),
            notes=["tesseract CLI not found on PATH"],
        )

    code, out, err = _run_bounded([path, "--version"])
    version_line = (out or err).splitlines()[0] if (out or err) else None
    if code != 0 or not version_line:
        missing.append("tesseract_version")
        notes.append("tesseract --version failed")

    _code_l, out_l, err_l = _run_bounded([path, "--list-langs"])
    langs_raw = (out_l or err_l).splitlines()
    langs = sorted(
        {
            line.strip()
            for line in langs_raw
            if line.strip() and "list of available languages" not in line.lower()
        }
    )
    missing_langs = [lang for lang in REQUIRED_OCR_LANGUAGES if lang not in langs]
    tessdata = os.environ.get("TESSDATA_PREFIX") or None
    measured = None
    if tessdata:
        tess_path = Path(tessdata)
        if tess_path.is_dir():
            measured = sum(p.stat().st_size for p in tess_path.glob("*.traineddata") if p.is_file())

    offline = bool(path and version_line and not missing_langs)
    if missing_langs:
        missing.extend(f"tessdata_{lang}" for lang in missing_langs)

    return OcrBackendAvailability(
        kind=OcrBackendKind.TESSERACT_CLI,
        installed=True,
        offline_ready=offline,
        version=version_line,
        executable_path=path,
        language_data_path=tessdata,
        installed_languages=langs,
        missing_languages=missing_langs,
        missing_components=missing,
        network_required=False,
        may_download=False,
        measured_local_artifact_bytes=measured,
        notes=notes,
    )


def probe_rapidocr_local() -> OcrBackendAvailability:
    """Probe RapidOCR package presence without initializing the engine."""
    present = _find_spec("rapidocr")
    onnx = _find_spec("onnxruntime")
    missing: list[str] = []
    notes: list[str] = []
    measured = 0
    if not present:
        return OcrBackendAvailability(
            kind=OcrBackendKind.RAPIDOCR_LOCAL,
            installed=False,
            offline_ready=False,
            missing_components=["rapidocr_package"],
            notes=["rapidocr not installed"],
        )
    ver = _pkg_version("rapidocr")
    if not onnx:
        missing.append("onnxruntime")
        notes.append("onnxruntime not installed; RapidOCR cannot run offline")
    try:
        import rapidocr

        root = Path(rapidocr.__file__).resolve().parent / "models"
        onnx_files = list(root.glob("*.onnx")) if root.is_dir() else []
        measured = sum(p.stat().st_size for p in onnx_files)
        cyrillic = list(root.glob("*cyrillic*"))
        if not cyrillic:
            missing.append("rapidocr_cyrillic_model")
            notes.append(
                "local RapidOCR models are Chinese PP-OCRv6 defaults; "
                "cyrillic recognizer is not present locally and may download"
            )
    except Exception as exc:
        missing.append("rapidocr_inspect_failed")
        notes.append(f"rapidocr inspect failed: {exc.__class__.__name__}")

    # RapidOCR is not selected for competition languages eng+rus+kaz without
    # local cyrillic + onnxruntime; treat as not offline-ready.
    return OcrBackendAvailability(
        kind=OcrBackendKind.RAPIDOCR_LOCAL,
        installed=True,
        offline_ready=False,
        version=ver,
        installed_languages=[],
        missing_languages=list(REQUIRED_OCR_LANGUAGES),
        missing_components=missing,
        network_required=True,
        may_download=True,
        measured_local_artifact_bytes=measured or None,
        notes=notes,
    )


def probe_ocr_environment() -> OcrProbeReport:
    """Aggregate read-only OCR probe. Never downloads models."""
    tesseract = probe_tesseract_cli()
    rapid = probe_rapidocr_local()
    candidates = [tesseract, rapid]
    selected = OcrBackendKind.NONE
    if tesseract.offline_ready:
        selected = OcrBackendKind.TESSERACT_CLI
    # No silent RapidOCR fallback for competition language set.
    other = {
        "docling": _pkg_version("docling"),
        "pypdfium2": _pkg_version("pypdfium2"),
        "rapidocr": _pkg_version("rapidocr"),
        "onnxruntime": _pkg_version("onnxruntime") if _find_spec("onnxruntime") else None,
        "pytesseract": _pkg_version("pytesseract") if _find_spec("pytesseract") else None,
        "easyocr": _pkg_version("easyocr") if _find_spec("easyocr") else None,
        "Pillow": _pkg_version("Pillow") if _find_spec("PIL") else None,
    }
    return OcrProbeReport(
        candidates=candidates,
        selected_kind=selected,
        offline_ready_backend=selected is not OcrBackendKind.NONE,
        downloads_performed=False,
        docling_version=other.get("docling"),
        other_packages=other,
    )
