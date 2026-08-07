"""Structured model gateway with budget, cache, retry, and fail-closed semantics."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from halyk_agent.domain.fact_extraction.constants import (
    FACT_SCHEMA_VERSION,
    MODEL_GATEWAY_VERSION,
    MODEL_PROMPT_VERSION,
)
from halyk_agent.domain.ids import deterministic_id, sha256_text
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
    """

    config: LlmGatewayConfig
    primary: StructuredExtractionProvider | None = None
    escalation: StructuredExtractionProvider | None = None
    mock: MockStructuredProvider | None = None
    _external_attempts: int = 0
    _thinking_escalations: int = 0
    _records: list[ModelCallRecord] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _sem: threading.Semaphore | None = field(default=None, init=False)
    _cache: DiskExtractionCache = field(init=False)

    def __post_init__(self) -> None:
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
        if self.escalation is None and self.config.allow_network:
            if self.config.escalation_provider == ProviderName.DEEPSEEK.value:
                self.escalation = DeepSeekStructuredProvider(
                    model=self.config.escalation_model,
                    timeout_seconds=self.config.timeout_seconds,
                    temperature=self.config.temperature,
                    thinking_enabled=True,
                    reasoning_effort="high",
                )
            elif self.config.escalation_provider == ProviderName.ANTHROPIC.value:
                # Experimental / not auto-selected by defaults.
                self.escalation = AnthropicStructuredProvider(
                    model=self.config.escalation_model,
                    timeout_seconds=self.config.timeout_seconds,
                    temperature=self.config.temperature,
                )

    @property
    def call_records(self) -> tuple[ModelCallRecord, ...]:
        return tuple(self._records)

    @property
    def external_attempt_count(self) -> int:
        return self._external_attempts

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
            "primary_ready": self._provider_ready(self.config.primary_provider),
            "escalation_ready": self._provider_ready(self.config.escalation_provider),
            "note": (
                "probe never performs HTTP; pass --allow-network to enable live calls elsewhere. "
                "xai/anthropic are experimental and not selected by default."
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
            )
            if not escalate_on_validation_failure:
                return cached, record
            result = cached
        else:
            with self._lock:
                if self._external_attempts >= self.config.max_external_attempts:
                    result = StructuredExtractionResult(
                        state=ExtractionState.BUDGET_EXCEEDED,
                        reason_code="MAX_EXTERNAL_ATTEMPTS",
                    )
                    record = self._record(request, provider_name, model_name, result, 1)
                    return result, record

            assert self._sem is not None
            self._sem.acquire()
            try:
                result, attempts_used = self._call_with_retry(provider, request)
            finally:
                self._sem.release()

            with self._lock:
                self._external_attempts += attempts_used

            record = self._record(request, provider_name, model_name, result, attempts_used)
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
                if self._external_attempts >= self.config.max_external_attempts:
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
                )
                return esc_cached, esc_record

            with self._lock:
                if self._external_attempts >= self.config.max_external_attempts:
                    return result, record
                self._thinking_escalations += 1

            assert self._sem is not None
            self._sem.acquire()
            try:
                esc_result, esc_attempts = self._call_with_retry(self.escalation, request)
            finally:
                self._sem.release()

            with self._lock:
                self._external_attempts += esc_attempts

            esc_record = self._record(
                request,
                esc_name,
                self.escalation.model,
                esc_result,
                esc_attempts,
                escalated=True,
            )
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

    def _call_with_retry(
        self,
        provider: StructuredExtractionProvider | None,
        request: StructuredExtractionRequest,
    ) -> tuple[StructuredExtractionResult, int]:
        """Call provider; every provider attempt (HTTP or mock) counts toward budget."""
        if provider is None:
            return (
                StructuredExtractionResult(
                    state=ExtractionState.UNRESOLVED,
                    reason_code="FAIL_CLOSED_NO_PROVIDER",
                ),
                0,
            )
        attempts = 1 + max(0, self.config.max_retries)
        last: StructuredExtractionResult | None = None
        used = 0
        for _ in range(attempts):
            with self._lock:
                if self._external_attempts + used >= self.config.max_external_attempts:
                    return (
                        StructuredExtractionResult(
                            state=ExtractionState.BUDGET_EXCEEDED,
                            reason_code="MAX_EXTERNAL_ATTEMPTS",
                        ),
                        used,
                    )
            before = getattr(provider, "http_calls", None)
            last = provider.extract(request)
            after = getattr(provider, "http_calls", None)
            if isinstance(before, int) and isinstance(after, int) and after >= before:
                delta = max(1, after - before)
            else:
                delta = 1
            used += delta
            if last.state is not ExtractionState.PROVIDER_ERROR:
                return last, used
        assert last is not None
        return last, used

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
            attempt=max(1, attempt),
            escalated=escalated,
            cache_hit=cache_hit,
            state=result.state if not cache_hit else ExtractionState.CACHE_HIT,
            error_code=result.reason_code
            if result.state
            not in {
                ExtractionState.RESOLVED,
                ExtractionState.UNRESOLVED,
                ExtractionState.CACHE_HIT,
            }
            else None,
        )
        self._records.append(record)
        return record
