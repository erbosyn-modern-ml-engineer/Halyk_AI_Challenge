"""Injectable audited file opener for competition solver."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from halyk_agent.solver.errors import LeakageAttemptError


@runtime_checkable
class FileOpener(Protocol):
    """Every production opener must expose an auditable open log."""

    @property
    def opened_paths(self) -> Sequence[Path]: ...

    def read_bytes(self, path: Path) -> bytes: ...


class RecordingFileOpener:
    """Filesystem opener that records every path opened by the solver."""

    def __init__(self) -> None:
        self._opened_paths: list[Path] = []

    @property
    def opened_paths(self) -> Sequence[Path]:
        return list(self._opened_paths)

    def read_bytes(self, path: Path) -> bytes:
        resolved = path.resolve()
        self._opened_paths.append(resolved)
        return resolved.read_bytes()


def require_audited_opener(opener: object) -> FileOpener:
    """Fail closed if opener does not satisfy the audited FileOpener contract."""
    if not isinstance(opener, FileOpener):
        raise LeakageAttemptError(
            "solver FileOpener must implement read_bytes and opened_paths; "
            "unaudited openers are rejected before any input is read"
        )
    # Touch property to ensure it is usable.
    _ = list(opener.opened_paths)
    return opener
