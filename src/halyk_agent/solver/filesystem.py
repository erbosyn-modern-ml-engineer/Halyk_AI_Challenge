"""Injectable audited file opener for competition solver."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from halyk_agent.dataset_access import (
    FileOpener as SharedFileOpener,
)
from halyk_agent.dataset_access import LeakageAttemptError as SharedLeakageAttemptError
from halyk_agent.dataset_access import (
    RecordingFileOpener,
)
from halyk_agent.dataset_access import (
    require_audited_opener as shared_require_audited_opener,
)
from halyk_agent.solver.errors import LeakageAttemptError

# Re-export shared protocol / opener under the solver package API.
FileOpener = SharedFileOpener


@runtime_checkable
class _FileOpenerCheck(Protocol):
    @property
    def opened_paths(self) -> Sequence[Path]: ...

    def read_bytes(self, path: Path) -> bytes: ...


def require_audited_opener(opener: object) -> SharedFileOpener:
    """Fail closed; raise solver.errors.LeakageAttemptError."""
    try:
        return shared_require_audited_opener(opener)
    except SharedLeakageAttemptError as exc:
        raise LeakageAttemptError(exc.message) from exc


__all__ = [
    "FileOpener",
    "RecordingFileOpener",
    "require_audited_opener",
]
