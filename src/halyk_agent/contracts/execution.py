"""Durable and direct job execution contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from halyk_agent.domain.common import JsonObject, NonEmptyStr


class JobStatus(StrEnum):
    """Terminal and non-terminal job states."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class JobSpec(BaseModel):
    """Specification for a unit of asynchronous work."""

    model_config = ConfigDict(extra="forbid")

    job_id: NonEmptyStr
    job_type: NonEmptyStr
    payload: JsonObject = Field(default_factory=dict)
    created_at: datetime | None = None
    timeout_seconds: float | None = Field(default=None, gt=0)


class JobHandle(BaseModel):
    """Handle returned after submitting a job."""

    model_config = ConfigDict(extra="forbid")

    job_id: NonEmptyStr
    status: JobStatus


@runtime_checkable
class JobExecutor(Protocol):
    """Submits and waits for jobs under a profile-specific execution backend."""

    async def submit(self, job: JobSpec) -> JobHandle:
        """Enqueue or start a job and return its handle."""
        ...

    async def wait_for_completion(self, job_id: str, *, timeout: float = 300.0) -> JobStatus:
        """Block until the job reaches a terminal status or times out."""
        ...

    async def start(self) -> None:
        """Start background workers if the backend requires them."""
        ...

    async def stop(self) -> None:
        """Stop workers gracefully and release leases/resources."""
        ...
