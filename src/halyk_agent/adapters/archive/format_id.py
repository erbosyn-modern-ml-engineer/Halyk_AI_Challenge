"""Lightweight file format identification from signatures and extensions."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from halyk_agent.domain.datasets import ArtifactFormat


def _read_prefix(path: Path, size: int = 16) -> bytes:
    with path.open("rb") as handle:
        return handle.read(size)


def _looks_like_utf_text(sample: bytes) -> bool:
    if not sample:
        return True
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _office_kind_from_zip(path: Path) -> ArtifactFormat | None:
    try:
        with zipfile.ZipFile(path) as zf:
            names = {name.replace("\\", "/") for name in zf.namelist()}
    except zipfile.BadZipFile:
        return None
    if any(name.startswith("word/") for name in names):
        return ArtifactFormat.DOCX
    if any(name.startswith("xl/") for name in names):
        return ArtifactFormat.XLSX
    return ArtifactFormat.ZIP


def detect_format(path: Path, *, relative_path: str) -> tuple[ArtifactFormat, list[str]]:
    """Detect artifact format using signatures with extension cross-checks."""
    warnings: list[str] = []
    suffix = path.suffix.lower()
    prefix = _read_prefix(path, 16)

    detected = ArtifactFormat.UNKNOWN
    if prefix.startswith(b"%PDF-"):
        detected = ArtifactFormat.PDF
    elif prefix.startswith(b"\xff\xd8\xff") or prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        detected = ArtifactFormat.IMAGE
    elif prefix.startswith(b"PK\x03\x04") or prefix.startswith(b"PK\x05\x06"):
        office = _office_kind_from_zip(path)
        detected = office or ArtifactFormat.ZIP
    elif suffix == ".jsonl":
        detected = ArtifactFormat.JSONL
    elif suffix == ".json":
        detected = ArtifactFormat.JSON
    elif suffix == ".csv":
        detected = ArtifactFormat.CSV
    elif suffix in {".txt", ".md", ".log"}:
        detected = ArtifactFormat.TXT
    elif _looks_like_utf_text(prefix):
        # Probe JSON/JSONL/CSV lightly for extensionless text.
        try:
            text = path.read_text(encoding="utf-8")
            if text.lstrip().startswith(("{", "[")):
                json.loads(text)
                detected = ArtifactFormat.JSON
            elif "\n" in text and text.lstrip().startswith("{"):
                detected = ArtifactFormat.JSONL
            elif "," in text or ";" in text or "\t" in text:
                detected = ArtifactFormat.CSV
            else:
                detected = ArtifactFormat.TXT
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            detected = (
                ArtifactFormat.TXT if _looks_like_utf_text(prefix) else ArtifactFormat.UNKNOWN
            )

    extension_map = {
        ".pdf": ArtifactFormat.PDF,
        ".docx": ArtifactFormat.DOCX,
        ".xlsx": ArtifactFormat.XLSX,
        ".csv": ArtifactFormat.CSV,
        ".json": ArtifactFormat.JSON,
        ".jsonl": ArtifactFormat.JSONL,
        ".txt": ArtifactFormat.TXT,
        ".zip": ArtifactFormat.ZIP,
        ".jpg": ArtifactFormat.IMAGE,
        ".jpeg": ArtifactFormat.IMAGE,
        ".png": ArtifactFormat.IMAGE,
    }
    expected = extension_map.get(suffix)
    office_ok = expected in {ArtifactFormat.DOCX, ArtifactFormat.XLSX} and detected in {
        ArtifactFormat.DOCX,
        ArtifactFormat.XLSX,
        ArtifactFormat.ZIP,
    }
    if (
        expected is not None
        and detected not in {ArtifactFormat.UNKNOWN, expected}
        and not office_ok
    ):
        warnings.append(
            f"extension/signature mismatch for {relative_path}: "
            f"extension suggests {expected.value}, detected {detected.value}"
        )
    if expected is not None and detected is ArtifactFormat.UNKNOWN:
        detected = expected
    if suffix == ".jsonl" and detected is ArtifactFormat.JSON:
        detected = ArtifactFormat.JSONL
    return detected, warnings


def guess_mime(format_: ArtifactFormat) -> str | None:
    """Return a stable MIME type for a detected format."""
    return {
        ArtifactFormat.PDF: "application/pdf",
        ArtifactFormat.DOCX: (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        ArtifactFormat.XLSX: ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ArtifactFormat.CSV: "text/csv",
        ArtifactFormat.JSON: "application/json",
        ArtifactFormat.JSONL: "application/x-ndjson",
        ArtifactFormat.TXT: "text/plain",
        ArtifactFormat.ZIP: "application/zip",
        ArtifactFormat.IMAGE: "application/octet-stream",
        ArtifactFormat.UNKNOWN: None,
    }[format_]
