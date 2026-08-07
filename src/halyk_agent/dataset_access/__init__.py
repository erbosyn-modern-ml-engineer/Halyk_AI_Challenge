"""Shared sanitized-dataset access boundary (Stage 5A / 5B).

Competition paths must open only allowlisted inputs via an audited FileOpener.
Never import preflight discover/quarantine implementations from here.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from halyk_agent.preflight.models import SanitizedDatasetManifest


class LeakageAttemptError(Exception):
    """Raised when a competition path attempts answer-key / GT access."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@runtime_checkable
class FileOpener(Protocol):
    """Every production opener must expose an auditable open log."""

    @property
    def opened_paths(self) -> Sequence[Path]: ...

    def read_bytes(self, path: Path) -> bytes: ...


class RecordingFileOpener:
    """Filesystem opener that records every resolved path opened."""

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
            "FileOpener must implement read_bytes and opened_paths; "
            "unaudited openers are rejected before any input is read"
        )
    try:
        opened = opener.opened_paths
    except Exception as exc:
        raise LeakageAttemptError(
            "FileOpener.opened_paths is unusable; "
            "unaudited openers are rejected before any input is read"
        ) from exc
    if opened is None:
        raise LeakageAttemptError(
            "FileOpener.opened_paths must be a sequence, not None; "
            "unaudited openers are rejected before any input is read"
        )
    try:
        _ = list(opened)
    except TypeError as exc:
        raise LeakageAttemptError(
            "FileOpener.opened_paths must be an iterable sequence; "
            "unaudited openers are rejected before any input is read"
        ) from exc
    return opener


def resolve_dataset_path(path: Path | str) -> Path:
    """Canonical path used for allowlist / quarantine membership."""
    return Path(path).expanduser().resolve()


def looks_like_ground_truth_name(path: Path) -> bool:
    return "ground_truth" in path.name.lower()


def validate_manifest_paths(
    manifest: SanitizedDatasetManifest,
) -> tuple[set[Path], set[Path]]:
    """Return (allowed, banned) resolved path sets. Fail if roles point at quarantine."""
    banned = {resolve_dataset_path(item.path) for item in manifest.quarantined}
    allowed = {
        resolve_dataset_path(manifest.submission_template.path),
        resolve_dataset_path(manifest.primary_ledger.path),
        *(resolve_dataset_path(case.path) for case in manifest.case_descriptions),
    }
    overlap = allowed & banned
    if overlap:
        paths = sorted(str(p) for p in overlap)
        raise LeakageAttemptError(f"sanitized manifest allowlist overlaps quarantine: {paths}")
    for path in allowed:
        if looks_like_ground_truth_name(path):
            raise LeakageAttemptError(f"allowlisted path looks like ground truth: {path}")
    return allowed, banned


def assert_opens_allowlisted(
    opener: FileOpener,
    *,
    allowed: set[Path],
    banned: set[Path],
) -> None:
    """Verify every recorded open is allowlisted and never quarantined."""
    opened = [resolve_dataset_path(p) for p in opener.opened_paths]
    for path in opened:
        if path in banned:
            raise LeakageAttemptError(f"opened quarantined path: {path}")
        if path not in allowed:
            raise LeakageAttemptError(f"opened non-allowlisted path: {path}")
