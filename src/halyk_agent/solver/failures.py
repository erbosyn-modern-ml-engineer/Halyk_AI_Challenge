"""Bounded failure-event vocabulary for Stage 5A diagnostics."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class FailureMode(StrEnum):
    TECHNICAL_ARTIFACT_IGNORED = "TECHNICAL_ARTIFACT_IGNORED"
    ANSWER_KEY_ACCESS_BLOCKED = "ANSWER_KEY_ACCESS_BLOCKED"
    DATASET_SCHEMA_INVALID = "DATASET_SCHEMA_INVALID"
    SUBMISSION_SCHEMA_INVALID = "SUBMISSION_SCHEMA_INVALID"
    GROUND_TRUTH_LEAKAGE_ATTEMPT = "GROUND_TRUTH_LEAKAGE_ATTEMPT"
    IMAGE_PAGE_UNREADABLE = "IMAGE_PAGE_UNREADABLE"
    OCR_REQUIRED = "OCR_REQUIRED"
    HEADING_WITHOUT_BODY = "HEADING_WITHOUT_BODY"
    OCR_BACKEND_UNAVAILABLE = "OCR_BACKEND_UNAVAILABLE"
    OCR_RESULT_INVALID = "OCR_RESULT_INVALID"


class FailureEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    run_id: str
    stage: str
    interaction_edge: str
    fault_side: str
    failure_mode: FailureMode
    observed_symptom: str
    earliest_unrecovered_event_id: str | None = None
    recovered: bool = False
    evidence_refs: list[str] = Field(default_factory=list)
    recommended_repair_owner: str = "solver"
