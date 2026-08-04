"""Archive inspection error hierarchy."""

from __future__ import annotations


class ArchiveInspectionError(Exception):
    """Base error for archive inspection failures."""


class UnsafeArchiveEntryError(ArchiveInspectionError):
    """An archive member violates path or file-type safety rules."""


class ArchiveLimitExceededError(ArchiveInspectionError):
    """An archive exceeds configured size, count, or ratio limits."""


class CorruptArchiveError(ArchiveInspectionError):
    """The top-level archive is structurally corrupt or unreadable."""


class DuplicateArchivePathError(UnsafeArchiveEntryError):
    """Two archive members normalize to the same relative path."""


class UnsupportedArchiveError(ArchiveInspectionError):
    """The archive format or operation is not supported."""
