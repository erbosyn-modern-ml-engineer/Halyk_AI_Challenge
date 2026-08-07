"""Provider protocol for structured extraction."""

from __future__ import annotations

from typing import Protocol

from halyk_agent.domain.models_gateway.types import (
    StructuredExtractionRequest,
    StructuredExtractionResult,
)


class StructuredExtractionProvider(Protocol):
    name: str
    model: str

    def extract(self, request: StructuredExtractionRequest) -> StructuredExtractionResult:
        """Extract a structured fact candidate from supplied fragments only."""
        ...
