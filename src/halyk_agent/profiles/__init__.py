"""Execution profile declarations (configuration only)."""

from __future__ import annotations

from halyk_agent.profiles.fast import FAST_PROFILE
from halyk_agent.profiles.full import FULL_PROFILE
from halyk_agent.profiles.types import (
    EvidenceDepth,
    ExecutionProfile,
    JobBackend,
    ParserMode,
    ProfileName,
    RetrievalMode,
    StorageBackend,
)

__all__ = [
    "FAST_PROFILE",
    "FULL_PROFILE",
    "EvidenceDepth",
    "ExecutionProfile",
    "JobBackend",
    "ParserMode",
    "ProfileName",
    "RetrievalMode",
    "StorageBackend",
    "load_profile",
]


def load_profile(name: ProfileName | str) -> ExecutionProfile:
    """Return the FAST or FULL profile declaration."""
    profile = ProfileName(name)
    if profile is ProfileName.FAST:
        return FAST_PROFILE
    if profile is ProfileName.FULL:
        return FULL_PROFILE
    raise ValueError(f"unsupported profile: {name!r}")
