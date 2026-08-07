"""Scriptable mock provider for offline Stage 5E tests."""

from __future__ import annotations

from collections.abc import Callable

from halyk_agent.domain.models_gateway.types import (
    ExtractionState,
    StructuredExtractionRequest,
    StructuredExtractionResult,
)

ResponseFactory = Callable[[StructuredExtractionRequest], StructuredExtractionResult]


class MockStructuredProvider:
    """Deterministic, scriptable provider — never performs HTTP."""

    name = "mock"
    model = "mock-model"

    def __init__(
        self,
        responses: dict[str, StructuredExtractionResult] | None = None,
        *,
        default_factory: ResponseFactory | None = None,
        model: str = "mock-model",
    ) -> None:
        self.model = model
        self._by_requirement = dict(responses or {})
        self._default = default_factory
        self.calls: list[StructuredExtractionRequest] = []

    def set_response(self, requirement_id: str, result: StructuredExtractionResult) -> None:
        self._by_requirement[requirement_id] = result

    def extract(self, request: StructuredExtractionRequest) -> StructuredExtractionResult:
        self.calls.append(request)
        if request.requirement_id in self._by_requirement:
            return self._by_requirement[request.requirement_id]
        if self._default is not None:
            return self._default(request)
        return StructuredExtractionResult(
            state=ExtractionState.UNRESOLVED,
            reason_code="MOCK_NO_RESPONSE",
        )
