"""Profile enums and configuration model."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ProfileName(StrEnum):
    """Runtime profile selector."""

    FAST = "fast"
    FULL = "full"


class StorageBackend(StrEnum):
    """Persistence backend used by a profile."""

    LOCAL = "local"
    POSTGRES = "postgres"


class JobBackend(StrEnum):
    """Job execution backend used by a profile."""

    ASYNCIO = "asyncio"
    REDIS_LEASE = "redis_lease"


class ParserMode(StrEnum):
    """Document parser mode."""

    FAST = "fast"
    QUALITY = "quality"


class RetrievalMode(StrEnum):
    """Retrieval backend mode."""

    LOCAL = "local"
    POSTGRES_HYBRID = "postgres_hybrid"


class EvidenceDepth(StrEnum):
    """Evidence gathering depth budget."""

    STANDARD = "standard"
    DEEP = "deep"


class ExecutionProfile(BaseModel):
    """Declarative profile configuration shared by composition roots."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: ProfileName
    storage_backend: StorageBackend
    job_backend: JobBackend
    parser_mode: ParserMode
    retrieval_mode: RetrievalMode
    evidence_depth: EvidenceDepth = Field(...)
