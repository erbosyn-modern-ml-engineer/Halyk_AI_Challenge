"""Archive path and ZIP-entry safety validation."""

from __future__ import annotations

import re
import stat
import unicodedata
from pathlib import Path, PurePosixPath, PureWindowsPath

from halyk_agent.adapters.archive.errors import (
    ArchiveLimitExceededError,
    DuplicateArchivePathError,
    UnsafeArchiveEntryError,
)
from halyk_agent.config import Settings

_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_UNC_RE = re.compile(r"^\\\\\\\\|^//")


def normalize_member_path(raw_name: str, *, max_path_length: int) -> str:
    """Normalize a ZIP member name to a safe relative POSIX path."""
    if "\x00" in raw_name:
        raise UnsafeArchiveEntryError(f"NUL character in archive path: {raw_name!r}")
    if not raw_name or raw_name.strip() == "":
        raise UnsafeArchiveEntryError("empty archive member path")

    normalized = unicodedata.normalize("NFC", raw_name.replace("\\", "/"))
    if normalized.startswith("/") or normalized.startswith("\\"):
        raise UnsafeArchiveEntryError(f"absolute archive path rejected: {raw_name!r}")
    if _DRIVE_RE.match(normalized) or _DRIVE_RE.match(raw_name):
        raise UnsafeArchiveEntryError(f"Windows drive path rejected: {raw_name!r}")
    if _UNC_RE.match(normalized) or normalized.startswith("//") or raw_name.startswith("\\\\"):
        raise UnsafeArchiveEntryError(f"UNC path rejected: {raw_name!r}")

    pure = PurePosixPath(normalized)
    if pure.is_absolute():
        raise UnsafeArchiveEntryError(f"absolute archive path rejected: {raw_name!r}")
    if any(part == ".." for part in pure.parts):
        raise UnsafeArchiveEntryError(f"path traversal rejected: {raw_name!r}")
    if any(part == "" for part in pure.parts if part is not None) and normalized.endswith("/"):
        # Directory trailing slash is allowed; strip for file identity later.
        pass

    cleaned = PurePosixPath(*[part for part in pure.parts if part not in ("", ".")])
    result = cleaned.as_posix().rstrip("/")
    if not result:
        raise UnsafeArchiveEntryError(f"empty normalized archive path: {raw_name!r}")
    if len(result) > max_path_length:
        raise ArchiveLimitExceededError(
            f"path length {len(result)} exceeds max_path_length={max_path_length}"
        )
    # Reject Windows-absolute interpretation of the same name.
    win = PureWindowsPath(raw_name)
    if win.is_absolute() or win.drive:
        raise UnsafeArchiveEntryError(f"Windows absolute path rejected: {raw_name!r}")
    return result


def ensure_within_root(root: Path, target: Path) -> Path:
    """Resolve target and ensure it remains under extraction root."""
    root_resolved = root.resolve()
    target_resolved = target.resolve()
    try:
        target_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise UnsafeArchiveEntryError(
            f"path escapes extraction root: {target} not under {root}"
        ) from exc
    return target_resolved


def casefold_key(path: str) -> str:
    """Case-insensitive duplicate detection key (Windows-safe)."""
    return unicodedata.normalize("NFC", path).casefold()


def register_normalized_path(
    seen: dict[str, str],
    normalized_path: str,
) -> None:
    """Track normalized paths and reject duplicates, including casefold collisions."""
    if normalized_path in seen:
        raise DuplicateArchivePathError(f"duplicate normalized path: {normalized_path}")
    key = casefold_key(normalized_path)
    for existing in seen:
        if casefold_key(existing) == key:
            raise DuplicateArchivePathError(
                f"case-insensitive duplicate path: {normalized_path} collides with {existing}"
            )
    seen[normalized_path] = normalized_path


def is_symlink_zipinfo(external_attr: int, create_system: int) -> bool:
    """Return True when ZipInfo external attributes indicate a symbolic link."""
    # Unix create_system == 3
    if create_system == 3:
        mode = (external_attr >> 16) & 0xFFFF
        return stat.S_ISLNK(mode)
    return False


def is_special_unix_file(external_attr: int, create_system: int) -> bool:
    """Reject device nodes and other unsupported special Unix file types."""
    if create_system != 3:
        return False
    mode = (external_attr >> 16) & 0xFFFF
    return stat.S_ISCHR(mode) or stat.S_ISBLK(mode) or stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode)


def validate_declared_sizes(
    *,
    file_count: int,
    file_name: str,
    file_size: int,
    compress_size: int,
    total_uncompressed: int,
    settings: Settings,
) -> None:
    """Validate ZIP metadata against configured safety limits."""
    if file_count > settings.max_archive_files:
        raise ArchiveLimitExceededError(
            f"archive file count {file_count} exceeds max_archive_files="
            f"{settings.max_archive_files}"
        )
    if file_size < 0 or compress_size < 0:
        raise UnsafeArchiveEntryError(f"negative size for {file_name!r}")
    if file_size > settings.max_single_file_bytes:
        raise ArchiveLimitExceededError(
            f"file {file_name!r} declared size {file_size} exceeds "
            f"max_single_file_bytes={settings.max_single_file_bytes}"
        )
    if total_uncompressed > settings.max_total_uncompressed_bytes:
        raise ArchiveLimitExceededError(
            f"total uncompressed size {total_uncompressed} exceeds "
            f"max_total_uncompressed_bytes={settings.max_total_uncompressed_bytes}"
        )
    effective_compressed = max(compress_size, 1)
    ratio = file_size / effective_compressed
    if file_size > 0 and ratio > settings.max_compression_ratio:
        raise ArchiveLimitExceededError(
            f"compression ratio {ratio:.2f} for {file_name!r} exceeds "
            f"max_compression_ratio={settings.max_compression_ratio}"
        )
