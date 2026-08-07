"""Scriptable mock provider for offline Stage 5E tests."""

from __future__ import annotations

import time
from collections.abc import Callable

from halyk_agent.domain.models_gateway.budget import ExternalAttemptBudget
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
        self.http_calls = 0

    def set_response(self, requirement_id: str, result: StructuredExtractionResult) -> None:
        self._by_requirement[requirement_id] = result

    def extract(
        self,
        request: StructuredExtractionRequest,
        *,
        budget: ExternalAttemptBudget | None = None,
    ) -> StructuredExtractionResult:
        if budget is not None and not budget.try_claim():
            return StructuredExtractionResult(
                state=ExtractionState.BUDGET_EXCEEDED,
                reason_code="MAX_EXTERNAL_ATTEMPTS",
                latency_ms=0,
            )
        started = time.perf_counter()
        self.calls.append(request)
        self.http_calls += 1
        if request.requirement_id in self._by_requirement:
            result = self._by_requirement[request.requirement_id]
        elif self._default is not None:
            result = self._default(request)
        else:
            result = StructuredExtractionResult(
                state=ExtractionState.UNRESOLVED,
                reason_code="MOCK_NO_RESPONSE",
            )
        if result.latency_ms is None:
            result = result.model_copy(
                update={"latency_ms": int((time.perf_counter() - started) * 1000)}
            )
        return result
