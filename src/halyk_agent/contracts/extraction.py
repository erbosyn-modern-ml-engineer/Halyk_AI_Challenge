"""Structured extraction contracts."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from halyk_agent.contracts.retrieval import EvidenceBundle
from halyk_agent.domain.common import JsonObject, NonEmptyStr
from halyk_agent.domain.facts import ExplicitFact


class ExtractionSchema(BaseModel):
    """Requested fact types for structured extraction."""

    model_config = ConfigDict(extra="forbid")

    fact_types: list[NonEmptyStr] = Field(min_length=1)
    instructions: NonEmptyStr | None = None
    parameters: JsonObject = Field(default_factory=dict)


@runtime_checkable
class FactExtractor(Protocol):
    """Extracts explicit facts that each reference at least one evidence span."""

    async def extract(
        self,
        evidence: EvidenceBundle,
        *,
        schema: ExtractionSchema,
    ) -> list[ExplicitFact]:
        """Extract structured facts grounded in the provided evidence."""
        ...
