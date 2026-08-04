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
    storage_backend=StorageBackend.POSTGRES,
    job_backend=JobBackend.REDIS_LEASE,
    parser_mode=ParserMode.QUALITY,
    retrieval_mode=RetrievalMode.POSTGRES_HYBRID,
    evidence_depth=EvidenceDepth.DEEP,
)
