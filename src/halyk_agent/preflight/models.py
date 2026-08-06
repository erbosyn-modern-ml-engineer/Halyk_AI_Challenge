"""Solver-safe sanitized dataset DTOs (no expected answer values)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class JsonCandidateRole(StrEnum):
    SUBMISSION_TEMPLATE = "SUBMISSION_TEMPLATE"
    QUARANTINED_ANSWER_KEY = "QUARANTINED_ANSWER_KEY"
    UNKNOWN_JSON = "UNKNOWN_JSON"


class AllowedInputRef(BaseModel):
    """Bounded metadata for a solver-allowlisted input file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    sha256: str
    size: int
    role: str


class QuarantinedRef(BaseModel):
    """Quarantine metadata only — never carries parsed expected answers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    sha256: str
    size: int
    role: JsonCandidateRole
    quarantine_reason: str


class IgnoredRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    sha256: str
    size: int
    ignore_rule: str


class SanitizedDatasetManifest(BaseModel):
    """Competition solver input: allowlisted paths only, no dataset-root traversal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "halyk.sanitized_dataset_manifest.v1"
    case_descriptions: list[AllowedInputRef] = Field(default_factory=list)
    primary_ledger: AllowedInputRef
    submission_template: AllowedInputRef
    documents_dir: str | None = None
    document_files: list[AllowedInputRef] = Field(default_factory=list)
    technical_noise: list[AllowedInputRef] = Field(default_factory=list)
    ignored: list[IgnoredRef] = Field(default_factory=list)
    quarantined: list[QuarantinedRef] = Field(default_factory=list)
