"""Read-only OCR environment probe (no downloads, no model init)."""

from __future__ import annotations

import importlib.util
import os
import re
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

_LANG_LINE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")


def _pkg_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def _find_spec(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def discover_tesseract_executable() -> str | None:
    """Find tesseract without mutating PATH.

    Order: PATH via shutil.which, then common install layouts derived from
    environment roots (LOCALAPPDATA / ProgramFiles). Never hard-codes a user home.
    """
    for name in ("tesseract", "tesseract.exe"):
        found = shutil.which(name)
        if found:
            return str(Path(found).resolve())

    candidates: list[Path] = []
    for root_key in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(root_key)
        if not root:
            continue
        candidates.append(Path(root) / "Programs" / "Tesseract-OCR" / "tesseract.exe")
        candidates.append(Path(root) / "Tesseract-OCR" / "tesseract.exe")
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve())
    return None


def discover_tessdata_dir(executable_path: str) -> Path | None:
    """Prefer tessdata beside the executable; then valid TESSDATA_PREFIX variants."""
    exe = Path(executable_path).resolve()
    sibling = exe.parent / "tessdata"
    if sibling.is_dir() and any(sibling.glob("*.traineddata")):
        return sibling

    raw = os.environ.get("TESSDATA_PREFIX")
    if not raw or not raw.strip():
        return sibling if sibling.is_dir() else None

    prefix = Path(raw)
    # Accept either .../tessdata or a parent that contains tessdata/.
    for candidate in (prefix, prefix / "tessdata"):
        if candidate.is_dir() and any(candidate.glob("*.traineddata")):
            return candidate.resolve()
    return sibling if sibling.is_dir() else None


def languages_from_tessdata_dir(tessdata_dir: Path | None) -> list[str]:
    if tessdata_dir is None or not tessdata_dir.is_dir():
        return []
    return sorted({p.stem for p in tessdata_dir.glob("*.traineddata") if p.is_file()})


def normalize_language_token(token: str) -> str | None:
    """Normalize a language line; ignore headers and path-like noise."""
    value = token.strip()
    if not value:
        return None
    lower = value.lower()
    if lower.startswith("list of available languages"):
        return None
    if "tessdata" in lower.replace("\\", "/"):
        # tessdata/eng, tessdata\eng, /path/tessdata/eng
        value = Path(value.replace("\\", "/")).name
    if not _LANG_LINE_RE.match(value):
        return None
    return value


def parse_list_langs_output(stdout: str, stderr: str) -> list[str]:
    """Parse `tesseract --list-langs` from stdout and/or stderr."""
    text = "\n".join(part for part in (stdout, stderr) if part)
    langs: set[str] = set()
    for line in text.splitlines():
        token = normalize_language_token(line)
        if token is not None:
            langs.add(token)
    return sorted(langs)


def _subprocess_env_without_broken_tessdata_prefix() -> dict[str, str]:
    """Copy process env but never inject an empty TESSDATA_PREFIX.

    An empty TESSDATA_PREFIX makes Windows Tesseract search a bogus build-time
    mingw path and report zero languages.
    """
    env = {k: v for k, v in os.environ.items() if k != "TESSDATA_PREFIX"}
    raw = os.environ.get("TESSDATA_PREFIX")
    if raw is not None and raw.strip():
        env["TESSDATA_PREFIX"] = raw
    return env


def _run_bounded(
    cmd: list[str],
    *,
    timeout: float = 5.0,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env if env is not None else _subprocess_env_without_broken_tessdata_prefix(),
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
    path = discover_tesseract_executable()
    missing: list[str] = []
    notes: list[str] = []
    if not path:
        return OcrBackendAvailability(
            kind=OcrBackendKind.TESSERACT_CLI,
            installed=False,
            offline_ready=False,
            missing_components=["tesseract_executable"],
            missing_languages=list(REQUIRED_OCR_LANGUAGES),
            notes=["tesseract CLI not found on PATH or common install layouts"],
        )

    env = _subprocess_env_without_broken_tessdata_prefix()
    tessdata = discover_tessdata_dir(path)
    # Prefer explicit --tessdata-dir over mutating caller environment.
    list_cmd = [path, "--list-langs"]
    version_cmd = [path, "--version"]
    if tessdata is not None:
        list_cmd = [path, "--tessdata-dir", str(tessdata), "--list-langs"]

    code, out, err = _run_bounded(version_cmd, env=env)
    version_line = None
    for stream in (out, err):
        for line in stream.splitlines():
            if line.strip():
                version_line = line.strip()
                break
        if version_line:
            break
    if code != 0 or not version_line:
        missing.append("tesseract_version")
        notes.append("tesseract --version failed")

    code_l, out_l, err_l = _run_bounded(list_cmd, env=env)
    if code_l != 0:
        notes.append(f"tesseract --list-langs failed rc={code_l}")
    cmd_langs = parse_list_langs_output(out_l, err_l)
    fs_langs = languages_from_tessdata_dir(tessdata)
    if fs_langs and cmd_langs and set(fs_langs) != set(cmd_langs):
        notes.append(
            f"tessdata filesystem/command language disagreement: fs={fs_langs} cmd={cmd_langs}"
        )
    # Safe reconciliation: require both sources when both available.
    if fs_langs and cmd_langs:
        effective = sorted(set(fs_langs) & set(cmd_langs))
    elif fs_langs:
        effective = fs_langs
        notes.append("using filesystem tessdata languages (command list empty/unavailable)")
    else:
        effective = cmd_langs

    missing_langs = [lang for lang in REQUIRED_OCR_LANGUAGES if lang not in effective]
    measured = None
    if tessdata is not None and tessdata.is_dir():
        measured = sum(p.stat().st_size for p in tessdata.glob("*.traineddata") if p.is_file())

    offline = bool(path and version_line and not missing_langs and not missing)
    if missing_langs:
        missing.extend(f"tessdata_{lang}" for lang in missing_langs)

    return OcrBackendAvailability(
        kind=OcrBackendKind.TESSERACT_CLI,
        installed=True,
        offline_ready=offline,
        version=version_line,
        executable_path=path,
        language_data_path=str(tessdata) if tessdata is not None else None,
        installed_languages=effective,
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
