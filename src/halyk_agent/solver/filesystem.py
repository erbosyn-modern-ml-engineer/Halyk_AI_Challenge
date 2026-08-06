"""Injectable file opener for competition solver (records every open)."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class FileOpener(Protocol):
    def read_bytes(self, path: Path) -> bytes: ...


class RecordingFileOpener:
    """Filesystem opener that records every path opened by the solver."""

    def __init__(self) -> None:
        self.opened_paths: list[Path] = []

    def read_bytes(self, path: Path) -> bytes:
        resolved = path.resolve()
        self.opened_paths.append(resolved)
        return resolved.read_bytes()
