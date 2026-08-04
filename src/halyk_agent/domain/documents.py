"""Document and version domain models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from halyk_agent.domain.common import NonEmptyStr


class DocumentVersionStatus(StrEnum):
    """Lifecycle status of an observed document version."""

    OBSERVED = "observed"
    EFFECTIVE = "effective"
    SUPERSEDED = "superseded"
    REVOKED = "revoked"
    UNKNOWN = "unknown"


class DocumentVersionRef(BaseModel):
    """Reference to a concrete document version observed in evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: NonEmptyStr
    version_id: NonEmptyStr
    source_file: NonEmptyStr
    observed_at: datetime
    status: DocumentVersionStatus
    published_at: datetime | None = None
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    supersedes_version_id: NonEmptyStr | None = None
    revoked_at: datetime | None = None

    @model_validator(mode="after")
    def _validate_effective_window(self) -> DocumentVersionRef:
        if (
            self.effective_from is not None
            and self.effective_to is not None
            and self.effective_to <= self.effective_from
        ):
            raise ValueError("effective_to must be later than effective_from")
        return self


class ApplicableVersionConflict(BaseModel):
    """Describes an unresolved version applicability conflict."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: NonEmptyStr
    candidate_version_ids: list[NonEmptyStr] = Field(min_length=1)
    reason: NonEmptyStr


class ApplicableVersionSet(BaseModel):
    """Result DTO for deterministic document-version applicability."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    as_of: datetime
    versions: list[DocumentVersionRef] = Field(default_factory=list)
    conflicts: list[ApplicableVersionConflict] = Field(default_factory=list)
