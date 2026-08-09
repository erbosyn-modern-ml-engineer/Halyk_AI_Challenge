"""FULL profile configuration declaration."""

from __future__ import annotations

from halyk_agent.profiles.types import (
    EvidenceDepth,
    ExecutionProfile,
    JobBackend,
    ParserMode,
    ProfileName,
    RetrievalMode,
    StorageBackend,
)

FULL_PROFILE = ExecutionProfile(
    name=ProfileName.FULL,
    storage_backend=StorageBackend.LOCAL,
    job_backend=JobBackend.ASYNCIO,
    parser_mode=ParserMode.QUALITY,
    retrieval_mode=RetrievalMode.LOCAL,
    evidence_depth=EvidenceDepth.DEEP,
)
