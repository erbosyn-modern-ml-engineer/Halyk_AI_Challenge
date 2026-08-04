"""FAST profile configuration declaration."""

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

FAST_PROFILE = ExecutionProfile(
    name=ProfileName.FAST,
    storage_backend=StorageBackend.LOCAL,
    job_backend=JobBackend.ASYNCIO,
    parser_mode=ParserMode.FAST,
    retrieval_mode=RetrievalMode.LOCAL,
    evidence_depth=EvidenceDepth.STANDARD,
)
