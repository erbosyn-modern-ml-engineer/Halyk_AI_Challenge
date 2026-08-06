"""Technical artifact ignore policies for competition datasets."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import BaseModel, ConfigDict

APPLEDOUBLE_MAGIC = b"\x00\x05\x16\x07"
TINY_TECHNICAL_MAX_BYTES = 64
TINY_TECHNICAL_SUFFIXES = frozenset({".db", ".ini", ".tmp", ".bak"})


class IgnoredArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    size: int
    sha256: str
    ignore_rule: str


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def classify_ignore(path: Path, data: bytes | None = None) -> str | None:
    """Return ignore_rule if path/bytes should be ignored, else None."""
    parts = {part.lower() for part in path.parts}
    if "__macosx" in parts:
        return "path_component=__MACOSX"
    name = path.name
    if name.startswith("._"):
        return "basename_appledouble_prefix"
    lowered = name.lower()
    if lowered in {".ds_store", "thumbs.db", "desktop.ini"}:
        return f"basename={lowered}"
    payload = data
    if payload is None and path.is_file():
        try:
            payload = path.read_bytes()[:8]
        except OSError:
            payload = None
    if payload is not None and payload.startswith(APPLEDOUBLE_MAGIC):
        return "appledouble_magic"
    if path.is_file():
        size = path.stat().st_size
        if size <= TINY_TECHNICAL_MAX_BYTES and path.suffix.lower() in TINY_TECHNICAL_SUFFIXES:
            return "tiny_technical_artifact"
    return None


def ignore_artifact(path: Path) -> IgnoredArtifact | None:
    rule = classify_ignore(path)
    if rule is None:
        return None
    try:
        data = path.read_bytes() if path.is_file() else b""
    except OSError:
        data = b""
    return IgnoredArtifact(
        path=str(path.as_posix()),
        size=len(data),
        sha256=_sha256(data),
        ignore_rule=rule,
    )
