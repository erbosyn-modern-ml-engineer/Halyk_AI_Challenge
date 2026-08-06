"""Opened-file audit for competition runs."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from halyk_agent.solver.errors import LeakageAttemptError


class OpenedFileRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    sha256: str
    size: int
    component: str
    purpose: str


class RunFileAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    files: list[OpenedFileRecord] = Field(default_factory=list)

    def record(
        self,
        path: Path,
        *,
        component: str,
        purpose: str,
        data: bytes | None = None,
    ) -> OpenedFileRecord:
        payload = data if data is not None else path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        rec = OpenedFileRecord(
            path=str(path.as_posix()),
            sha256=digest,
            size=len(payload),
            component=component,
            purpose=purpose,
        )
        self.files.append(rec)
        return rec

    def assert_no_ground_truth(self, banned_names: set[str] | None = None) -> None:
        banned = banned_names or {"ground_truth.json"}
        for rec in self.files:
            name = Path(rec.path).name.lower()
            if name in banned or "ground_truth" in name:
                raise LeakageAttemptError(f"ground truth path appeared in solver audit: {rec.path}")
            if rec.purpose.lower() in {"ground_truth", "answer_key"}:
                raise LeakageAttemptError(
                    f"ground truth purpose recorded in solver audit: {rec.path}"
                )
