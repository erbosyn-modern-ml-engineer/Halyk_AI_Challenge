"""Structured model gateway with budget, cache, retry, and fail-closed semantics."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from halyk_agent.domain.fact_extraction.constants import (
    DEFAULT_DEEPSEEK_MAX_TOKENS,
    DEFAULT_PROVIDER_REVISION,
    FACT_SCHEMA_VERSION,
    FACT_VALIDATOR_VERSION,
    MODEL_GATEWAY_VERSION,
    MODEL_PROMPT_VERSION,
)
from halyk_agent.domain.ids import deterministic_id, sha256_text
from halyk_agent.domain.models_gateway.budget import ExternalAttemptBudget
from halyk_agent.domain.models_gateway.cache import DiskExtractionCache, cache_key
from halyk_agent.domain.models_gateway.providers.anthropic import AnthropicStructuredProvider
from halyk_agent.domain.models_gateway.providers.base import StructuredExtractionProvider
from halyk_agent.domain.models_gateway.providers.deepseek import DeepSeekStructuredProvider
from halyk_agent.domain.models_gateway.providers.mock import MockStructuredProvider
from halyk_agent.domain.models_gateway.providers.xai import XaiStructuredProvider
from halyk_agent.domain.models_gateway.types import (
    ExtractionState,
    ModelCallRecord,
    ProviderName,
    StructuredExtractionRequest,
    StructuredExtractionResult,
)


@dataclass
class LlmGatewayConfig:
    primary_provider: str = "deepseek"
    primary_model: str = "deepseek-v4-flash"
    escalation_provider: str = "deepseek"
    escalation_model: str = "deepseek-v4-flash"
    timeout_seconds: float = 60.0
    # Real HTTP budget: every network attempt (including retries/escalation) counts.
    max_external_attempts: int = 8
    max_thinking_escalations: int = 2
    max_concurrency: int = 2
    max_retries: int = 1
    temperature: float = 0.0
    max_tokens: int = DEFAULT_DEEPSEEK_MAX_TOKENS
    # Cache identity only — never sent as API model id.
    provider_revision: str = DEFAULT_PROVIDER_REVISION
    cache_dir: Path | None = None
    allow_network: bool = False
    # Deprecated alias retained for older call sites / tests.
    max_calls: int | None = None
    escalation_max_calls: int | None = None

    def __post_init__(self) -> None:
        if self.max_calls is not None:
            self.max_external_attempts = self.max_calls
        if self.escalation_max_calls is not None:
            self.max_thinking_escalations = self.escalation_max_calls


@dataclass
class StructuredModelGateway:
    """
    Fail-closed gateway: network disabled by default.

    Escalation uses DeepSeek thinking mode (enabled + reasoning_effort=high) after
    primary returns a candidate that later fails semantic/evidence validation.
    Never escalate when evidence is absent / UNRESOLVED.

    ExternalAttemptBudget is claimed BEFORE each HTTP request (including empty-content
    retries). max_external_attempts=N never allows an (N+1)th HTTP.
    """

    config: LlmGatewayConfig
    primary: StructuredExtractionProvider | None = None
    escalation: StructuredExtractionProvider | None = None
    mock: MockStructuredProvider | None = None
    shared_budget: ExternalAttemptBudget | None = None
    _budget: ExternalAttemptBudget = field(init=False)
    _thinking_escalations: int = 0
    _records: list[ModelCallRecord] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _sem: threading.Semaphore | None = field(default=None, init=False)
    _cache: DiskExtractionCache = field(init=False)

    def __post_init__(self) -> None:
        self._budget = self.shared_budget or ExternalAttemptBudget(
            max_attempts=self.config.max_external_attempts
        )
        self._cache = DiskExtractionCache(self.config.cache_dir)
        self._sem = threading.Semaphore(max(1, self.config.max_concurrency))
        if self.primary is None and self.mock is None:
            if self.config.primary_provider == ProviderName.MOCK.value:
                self.mock = MockStructuredProvider(model=self.config.primary_model)
            elif (
                self.config.allow_network
                and self.config.primary_provider == ProviderName.DEEPSEEK.value
            ):
                self.primary = DeepSeekStructuredProvider(
                    model=self.config.primary_model,
                    timeout_seconds=self.config.timeout_seconds,
                    temperature=self.config.temperature,
                    thinking_enabled=False,
                    max_tokens=self.config.max_tokens,
                    budget=self._budget,
                )
            elif (
                self.config.allow_network and self.config.primary_provider == ProviderName.XAI.value
            ):
                # Experimental / not auto-selected by defaults.
                self.primary = XaiStructuredProvider(
                    model=self.config.primary_model,
                    timeout_seconds=self.config.timeout_seconds,
                    temperature=self.config.temperature,
                )
        if isinstance(self.primary, DeepSeekStructuredProvider) and self.primary.budget is None:
            self.primary.budget = self._budget
        if self.escalation is None and self.config.allow_network:
            if self.config.escalation_provider == ProviderName.DEEPSEEK.value:
                self.escalation = DeepSeekStructuredProvider(
                    model=self.config.escalation_model,
                    timeout_seconds=self.config.timeout_seconds,
                    temperature=self.config.temperature,
                    thinking_enabled=True,
                    reasoning_effort="high",
                    max_tokens=self.config.max_tokens,
                    budget=self._budget,
                )
            elif self.config.escalation_provider == ProviderName.ANTHROPIC.value:
                # Experimental / not auto-selected by defaults.
                self.escalation = AnthropicStructuredProvider(
                    model=self.config.escalation_model,
                    timeout_seconds=self.config.timeout_seconds,
                    temperature=self.config.temperature,
                )
        if (
            isinstance(self.escalation, DeepSeekStructuredProvider)
            and self.escalation.budget is None
        ):
            self.escalation.budget = self._budget

    @property
    def call_records(self) -> tuple[ModelCallRecord, ...]:
        return tuple(self._records)

    @property
    def external_attempt_count(self) -> int:
        return self._budget.used

    @property
    def budget(self) -> ExternalAttemptBudget:
        return self._budget

    def probe(self) -> dict[str, Any]:
        """Describe configured providers without making network calls."""
        return {
            "gateway_version": MODEL_GATEWAY_VERSION,
            "allow_network": self.config.allow_network,
            "primary_provider": self.config.primary_provider,
            "primary_model": self.config.primary_model,
            "escalation_provider": self.config.escalation_provider,
            "escalation_model": self.config.escalation_model,
            "max_external_attempts": self.config.max_external_attempts,
            "max_thinking_escalations": self.config.max_thinking_escalations,
            "max_tokens": self.config.max_tokens,
            "provider_revision": self.config.provider_revision,
            "primary_ready": self._provider_ready(self.config.primary_provider),
            "escalation_ready": self._provider_ready(self.config.escalation_provider),
            "note": (
                "probe never performs HTTP; pass --allow-network to enable live calls elsewhere. "
                "xai/anthropic are experimental and not selected by default. "
                "provider_revision is cache identity only; API model remains deepseek-v4-flash."
            ),
        }

    def _provider_ready(self, name: str) -> bool:
        import os

        if name == ProviderName.MOCK.value:
            return True
        if name == ProviderName.DEEPSEEK.value:
            return bool(os.environ.get("DEEPSEEK_API_KEY"))
        if name == ProviderName.XAI.value:
            return bool(os.environ.get("XAI_API_KEY"))
        if name == ProviderName.ANTHROPIC.value:
            return bool(os.environ.get("ANTHROPIC_API_KEY"))
        return False

    def _active_primary(self) -> StructuredExtractionProvider | None:
        if self.mock is not None and self.config.primary_provider == ProviderName.MOCK.value:
            return self.mock
        if self.mock is not None and not self.config.allow_network:
            return self.mock
        return self.primary

    def _gen_config(
        self, *, thinking_enabled: bool, reasoning_effort: str | None
    ) -> dict[str, Any]:
        return {
            "temperature": self.config.temperature,
            "max_retries": self.config.max_retries,
            "thinking_enabled": thinking_enabled,
            "reasoning_effort": reasoning_effort or "",
            "max_tokens": self.config.max_tokens,
            "provider_revision": self.config.provider_revision,
            "validator_version": FACT_VALIDATOR_VERSION,
        }

    def _provider_name(self, name: str) -> ProviderName:
        if name in ProviderName._value2member_map_:
            return ProviderName(name)
        return ProviderName.MOCK

    def extract(
        self,
        request: StructuredExtractionRequest,
        *,
        escalate_on_validation_failure: bool = False,
    ) -> tuple[StructuredExtractionResult, ModelCallRecord]:
        if not self.config.allow_network and self._active_primary() is None:
            result = StructuredExtractionResult(
                state=ExtractionState.UNRESOLVED,
                reason_code="FAIL_CLOSED_NETWORK_DISABLED",
            )
            record = self._record(
                request, ProviderName.DEEPSEEK, self.config.primary_model, result, 1
            )
            return result, record

        provider = self._active_primary()
        provider_name = self._provider_name(self.config.primary_provider)
        model_name = provider.model if provider is not None else self.config.primary_model
        thinking_enabled = False
        reasoning_effort: str | None = None
        if isinstance(provider, DeepSeekStructuredProvider):
            thinking_enabled = provider.thinking_enabled
            reasoning_effort = provider.reasoning_effort

        gen_config = self._gen_config(
            thinking_enabled=thinking_enabled, reasoning_effort=reasoning_effort
        )
        key = cache_key(
            provider=provider_name.value,
            model=model_name,
            prompt_version=request.prompt_version,
            schema_version=request.schema_version,
            requirement_id=request.requirement_id,
            source_sha=request.source_sha256,
            window_hash=request.window_hash,
            gen_config=gen_config,
        )
        cached = self._cache.get(key, expected=request)
        if cached is not None:
            record = self._record(
                request,
                provider_name,
                model_name,
                cached,
                1,
                cache_hit=True,
                thinking_enabled=thinking_enabled,
                reasoning_effort=reasoning_effort,
                latency_ms=0,
            )
            if not escalate_on_validation_failure:
                return cached, record
            result = cached
        else:
            if self._budget.remaining <= 0 and self._budget.max_attempts == 0:
                result = StructuredExtractionResult(
                    state=ExtractionState.BUDGET_EXCEEDED,
                    reason_code="MAX_EXTERNAL_ATTEMPTS",
                )
                record = self._record(
                    request,
                    provider_name,
                    model_name,
                    result,
                    1,
                    thinking_enabled=thinking_enabled,
                    reasoning_effort=reasoning_effort,
                )
                return result, record
            if self._budget.used >= self.config.max_external_attempts:
                result = StructuredExtractionResult(
                    state=ExtractionState.BUDGET_EXCEEDED,
                    reason_code="MAX_EXTERNAL_ATTEMPTS",
                )
                record = self._record(
                    request,
                    provider_name,
                    model_name,
                    result,
                    1,
                    thinking_enabled=thinking_enabled,
                    reasoning_effort=reasoning_effort,
                )
                return result, record

            assert self._sem is not None
            self._sem.acquire()
            try:
                result = self._call_with_retry(provider, request)
            finally:
                self._sem.release()

            record = self._record(
                request,
                provider_name,
                model_name,
                result,
                1,
                thinking_enabled=thinking_enabled,
                reasoning_effort=reasoning_effort,
                latency_ms=result.latency_ms,
            )
            if result.state is not ExtractionState.BUDGET_EXCEEDED:
                self._cache.put(
                    key,
                    request=request,
                    result=result,
                    provider=provider_name.value,
                    model=model_name,
                    gen_config=gen_config,
                )

        if (
            escalate_on_validation_failure
            and result.state is ExtractionState.RESOLVED
            and bool(result.evidence_fragment_ids)
            and self.escalation is not None
            and (self.config.allow_network or self.mock is not None)
        ):
            with self._lock:
                if self._thinking_escalations >= self.config.max_thinking_escalations:
                    return result, record
                if self._budget.used >= self.config.max_external_attempts:
                    return result, record

            esc_thinking = True
            esc_effort = "high"
            if isinstance(self.escalation, DeepSeekStructuredProvider):
                esc_thinking = self.escalation.thinking_enabled
                esc_effort = self.escalation.reasoning_effort or "high"
            elif self.config.escalation_provider == ProviderName.MOCK.value:
                esc_thinking = True
                esc_effort = "high"

            esc_gen = self._gen_config(thinking_enabled=esc_thinking, reasoning_effort=esc_effort)
            esc_name = self._provider_name(self.config.escalation_provider)
            esc_key = cache_key(
                provider=esc_name.value,
                model=self.escalation.model,
                prompt_version=request.prompt_version,
                schema_version=request.schema_version,
                requirement_id=request.requirement_id,
                source_sha=request.source_sha256,
                window_hash=request.window_hash,
                gen_config=esc_gen,
            )
            esc_cached = self._cache.get(esc_key, expected=request)
            if esc_cached is not None:
                esc_record = self._record(
                    request,
                    esc_name,
                    self.escalation.model,
                    esc_cached,
                    1,
                    cache_hit=True,
                    escalated=True,
                    thinking_enabled=esc_thinking,
                    reasoning_effort=esc_effort,
                    latency_ms=0,
                )
                return esc_cached, esc_record

            with self._lock:
                if self._budget.used >= self.config.max_external_attempts:
                    return result, record
                self._thinking_escalations += 1

            assert self._sem is not None
            self._sem.acquire()
            try:
                esc_result = self._call_with_retry(self.escalation, request)
            finally:
                self._sem.release()

            esc_record = self._record(
                request,
                esc_name,
                self.escalation.model,
                esc_result,
                1,
                escalated=True,
                thinking_enabled=esc_thinking,
                reasoning_effort=esc_effort,
                latency_ms=esc_result.latency_ms,
            )
            if esc_result.state is not ExtractionState.BUDGET_EXCEEDED:
                self._cache.put(
                    esc_key,
                    request=request,
                    result=esc_result,
                    provider=esc_name.value,
                    model=self.escalation.model,
                    gen_config=esc_gen,
                )
            return esc_result, esc_record

        return result, record

    def _call_provider(
        self,
        provider: StructuredExtractionProvider,
        request: StructuredExtractionRequest,
    ) -> StructuredExtractionResult:
        """Invoke provider; DeepSeek claims budget itself; mocks claim once here."""
        if isinstance(provider, DeepSeekStructuredProvider):
            return provider.extract(request, budget=self._budget)
        if isinstance(provider, MockStructuredProvider):
            return provider.extract(request, budget=self._budget)
        # Non-DeepSeek experimental providers: claim once before call.
        if not self._budget.try_claim():
            return StructuredExtractionResult(
                state=ExtractionState.BUDGET_EXCEEDED,
                reason_code="MAX_EXTERNAL_ATTEMPTS",
            )
        started = time.perf_counter()
        result = provider.extract(request)
        if result.latency_ms is None:
            result = result.model_copy(
                update={"latency_ms": int((time.perf_counter() - started) * 1000)}
            )
        return result

    def _call_with_retry(
        self,
        provider: StructuredExtractionProvider | None,
        request: StructuredExtractionRequest,
    ) -> StructuredExtractionResult:
        """Call provider; retries are additional budget-consuming attempts for mocks."""
        if provider is None:
            return StructuredExtractionResult(
                state=ExtractionState.UNRESOLVED,
                reason_code="FAIL_CLOSED_NO_PROVIDER",
            )
        # DeepSeek owns its empty-content retry + budget claims internally.
        if isinstance(provider, DeepSeekStructuredProvider):
            return self._call_provider(provider, request)

        attempts = 1 + max(0, self.config.max_retries)
        last = StructuredExtractionResult(
            state=ExtractionState.UNRESOLVED,
            reason_code="FAIL_CLOSED_NO_PROVIDER",
        )
        for _ in range(attempts):
            if self._budget.used >= self.config.max_external_attempts:
                return StructuredExtractionResult(
                    state=ExtractionState.BUDGET_EXCEEDED,
                    reason_code="MAX_EXTERNAL_ATTEMPTS",
                )
            last = self._call_provider(provider, request)
            if last.state is not ExtractionState.PROVIDER_ERROR:
                return last
        return last

    def _record(
        self,
        request: StructuredExtractionRequest,
        provider: ProviderName,
        model: str,
        result: StructuredExtractionResult,
        attempt: int,
        *,
        cache_hit: bool = False,
        escalated: bool = False,
        thinking_enabled: bool = False,
        reasoning_effort: str | None = None,
        latency_ms: int | None = None,
    ) -> ModelCallRecord:
        req_hash = sha256_text(
            json.dumps(
                {
                    "requirement_id": request.requirement_id,
                    "window_hash": request.window_hash,
                    "source_sha256": request.source_sha256,
                    "fact_kind": request.fact_kind,
                },
                sort_keys=True,
            )
        )
        record = ModelCallRecord(
            call_id=deterministic_id(
                "model-call",
                request.requirement_id,
                provider.value,
                model,
                req_hash,
                str(attempt),
                str(len(self._records)),
            ),
            provider=provider,
            model=model,
            prompt_version=request.prompt_version or MODEL_PROMPT_VERSION,
            schema_version=request.schema_version or FACT_SCHEMA_VERSION,
            requirement_id=request.requirement_id,
            source_sha256=request.source_sha256,
            window_hash=request.window_hash,
            request_hash=req_hash,
            attempt=max(1, attempt if attempt < 10_000 else 1),
            escalated=escalated,
            cache_hit=cache_hit,
            state=result.state if not cache_hit else ExtractionState.CACHE_HIT,
            latency_ms=0
            if cache_hit and latency_ms is None
            else (latency_ms if latency_ms is not None else result.latency_ms),
            error_code=result.reason_code
            if result.state
            not in {
                ExtractionState.RESOLVED,
                ExtractionState.UNRESOLVED,
                ExtractionState.CACHE_HIT,
            }
            else None,
            usage=result.usage,
            thinking_enabled=thinking_enabled,
            reasoning_effort=reasoning_effort,
        )
        self._records.append(record)
        return record
