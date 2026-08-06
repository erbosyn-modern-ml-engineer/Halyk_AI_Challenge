"""Dataset discovery models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from halyk_agent.solver.dataset.ignore import IgnoredArtifact


class DatasetFileRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    sha256: str
    size: int
    role: str


class DatasetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    root: str
    case_descriptions: list[DatasetFileRef] = Field(default_factory=list)
    primary_ledger: DatasetFileRef | None = None
    submission_template: DatasetFileRef | None = None
    documents_dir: str | None = None
    document_files: list[DatasetFileRef] = Field(default_factory=list)
    ground_truth_candidate: DatasetFileRef | None = None
    technical_noise: list[DatasetFileRef] = Field(default_factory=list)
    ignored: list[IgnoredArtifact] = Field(default_factory=list)
