"""Profile configuration declaration tests."""

from __future__ import annotations

import pytest

from halyk_agent.config import Settings, get_settings
from halyk_agent.profiles import (
    EvidenceDepth,
    JobBackend,
    ParserMode,
    ProfileName,
    RetrievalMode,
    StorageBackend,
    load_profile,
)


def test_fast_profile_loads_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HALYK_PROFILE", "fast")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.profile is ProfileName.FAST
    profile = settings.execution_profile()
    assert profile.name is ProfileName.FAST
    assert profile.storage_backend is StorageBackend.LOCAL
    assert profile.job_backend is JobBackend.ASYNCIO
    assert profile.parser_mode is ParserMode.FAST
    assert profile.retrieval_mode is RetrievalMode.LOCAL
    assert profile.evidence_depth is EvidenceDepth.STANDARD


def test_full_profile_declaration_without_service_connections() -> None:
    """FULL profile is a pure declaration and must not open DB/Redis connections."""
    profile = load_profile(ProfileName.FULL)
    settings = Settings(profile=ProfileName.FULL)
    assert settings.execution_profile() == profile
    assert profile.storage_backend is StorageBackend.POSTGRES
    assert profile.job_backend is JobBackend.REDIS_LEASE
    assert profile.parser_mode is ParserMode.QUALITY
    assert profile.retrieval_mode is RetrievalMode.POSTGRES_HYBRID
    assert profile.evidence_depth is EvidenceDepth.DEEP
    assert settings.postgres_dsn is None
    assert settings.redis_url is None
