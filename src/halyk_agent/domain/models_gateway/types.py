"""Typed contracts for the Stage 5E structured model gateway."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from halyk_agent.domain.common import NonEmptyStr
from halyk_agent.domain.fact_extraction.constants import (
    FACT_SCHEMA_VERSION,
    MODEL_PROMPT_VERSION,
)


class ProviderName(StrEnum):
    XAI = "xai"
    ANTHROPIC = "anthropic"
    MOCK = "mock"


class ExtractionState(StrEnum):
    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    NETWORK_DISABLED = "NETWORK_DISABLED"
    CACHE_HIT = "CACHE_HIT"


class StructuredExtractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement_id: NonEmptyStr
    scenario_id: NonEmptyStr
    fact_kind: NonEmptyStr
    authority_domain: NonEmptyStr
    source_document_id: NonEmptyStr
    source_sha256: NonEmptyStr
    window_hash: NonEmptyStr
    fragments: tuple[dict[str, Any], ...]
    lexical_cues: tuple[str, ...] = ()
    prompt_version: NonEmptyStr = MODEL_PROMPT_VERSION
    schema_version: NonEmptyStr = FACT_SCHEMA_VERSION
    temperature: float = 0.0


class StructuredExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: ExtractionState
    payload: dict[str, Any] | None = None
    evidence_fragment_ids: tuple[NonEmptyStr, ...] = ()
    quote: NonEmptyStr | None = None
    page_number: int | None = Field(default=None, ge=1)
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)
    reason_code: NonEmptyStr = "OK"
    confidence: str | None = None
    raw_response_hash: NonEmptyStr | None = None


class ModelCallRecord(BaseModel):
    """Auditable model call metadata — never contains API keys or secrets."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    call_id: NonEmptyStr
    provider: ProviderName
    model: NonEmptyStr
    prompt_version: NonEmptyStr
    schema_version: NonEmptyStr
    requirement_id: NonEmptyStr
    source_sha256: NonEmptyStr
    window_hash: NonEmptyStr
    request_hash: NonEmptyStr
    attempt: int = Field(ge=1)
    escalated: bool = False
    cache_hit: bool = False
    state: ExtractionState
    latency_ms: int | None = Field(default=None, ge=0)
    error_code: str | None = None
    # Explicitly forbid secret-looking fields via extra=forbid; no api_key field.
