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
    primary_provider: str = "xai"
    primary_model: str = "grok-4.5"
    escalation_provider: str = "anthropic"
    escalation_model: str = "claude-opus-5"
    timeout_seconds: float = 60.0
    max_calls: int = 50
    max_concurrency: int = 2
    max_retries: int = 1
    escalation_max_calls: int = 5
    temperature: float = 0.0
    cache_dir: Path | None = None
    allow_network: bool = False


@dataclass
class StructuredModelGateway:
    """
    Fail-closed gateway: network disabled by default.

    Escalation only after primary returns a candidate that later fails
    semantic/evidence validation (caller signals via ``escalate_on_validation_failure``).
    Never escalate when evidence is absent / UNRESOLVED.
    """

    config: LlmGatewayConfig
    primary: StructuredExtractionProvider | None = None
    escalation: StructuredExtractionProvider | None = None
    mock: MockStructuredProvider | None = None
    _call_count: int = 0
    _escalation_count: int = 0
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
                self.config.allow_network and self.config.primary_provider == ProviderName.XAI.value
            ):
                self.primary = XaiStructuredProvider(
                    model=self.config.primary_model,
                    timeout_seconds=self.config.timeout_seconds,
                    temperature=self.config.temperature,
                )
        if (
            self.escalation is None
            and self.config.allow_network
            and self.config.escalation_provider == ProviderName.ANTHROPIC.value
        ):
            self.escalation = AnthropicStructuredProvider(
                model=self.config.escalation_model,
                timeout_seconds=self.config.timeout_seconds,
                temperature=self.config.temperature,
            )

    @property
    def call_records(self) -> tuple[ModelCallRecord, ...]:
        return tuple(self._records)

    def probe(self) -> dict[str, Any]:
        """Describe configured providers without making network calls."""
        return {
            "gateway_version": MODEL_GATEWAY_VERSION,
            "allow_network": self.config.allow_network,
            "primary_provider": self.config.primary_provider,
            "primary_model": self.config.primary_model,
            "escalation_provider": self.config.escalation_provider,
            "escalation_model": self.config.escalation_model,
            "max_calls": self.config.max_calls,
            "primary_ready": self._provider_ready(self.config.primary_provider),
            "escalation_ready": self._provider_ready(self.config.escalation_provider),
            "note": (
                "probe never performs HTTP; pass --allow-network to enable live calls elsewhere"
            ),
        }

    def _provider_ready(self, name: str) -> bool:
        import os

        if name == ProviderName.MOCK.value:
            return True
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
            record = self._record(request, ProviderName.XAI, self.config.primary_model, result, 1)
            return result, record

        provider = self._active_primary()
        provider_name = (
            ProviderName(self.config.primary_provider)
            if self.config.primary_provider in ProviderName._value2member_map_
            else ProviderName.MOCK
        )
        model_name = provider.model if provider is not None else self.config.primary_model

        gen_config = {
            "temperature": self.config.temperature,
            "max_retries": self.config.max_retries,
        }
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
                if self._call_count >= self.config.max_calls:
                    result = StructuredExtractionResult(
                        state=ExtractionState.BUDGET_EXCEEDED,
                        reason_code="MAX_CALLS",
                    )
                    record = self._record(request, provider_name, model_name, result, 1)
                    return result, record
                self._call_count += 1

            assert self._sem is not None
            self._sem.acquire()
            try:
                result = self._call_with_retry(provider, request)
            finally:
                self._sem.release()

            record = self._record(request, provider_name, model_name, result, 1)
            self._cache.put(
                key,
                request=request,
                result=result,
                provider=provider_name.value,
                model=model_name,
            )

        # Escalation: only when caller says validation failed AFTER a resolved payload
        # with evidence fragment ids — never when evidence is absent / UNRESOLVED.
        if (
            escalate_on_validation_failure
            and result.state is ExtractionState.RESOLVED
            and bool(result.evidence_fragment_ids)
            and self.escalation is not None
            and (self.config.allow_network or self.mock is not None)
        ):
            with self._lock:
                if self._escalation_count >= self.config.escalation_max_calls:
                    return result, record
                if self._call_count >= self.config.max_calls:
                    return result, record
                self._escalation_count += 1
                self._call_count += 1
            assert self._sem is not None
            self._sem.acquire()
            try:
                esc_result = self.escalation.extract(request)
            finally:
                self._sem.release()
            esc_name = (
                ProviderName.ANTHROPIC
                if self.config.escalation_provider == ProviderName.ANTHROPIC.value
                else ProviderName.MOCK
            )
            esc_record = self._record(
                request,
                esc_name,
                self.escalation.model,
                esc_result,
                1,
                escalated=True,
            )
            return esc_result, esc_record

        return result, record

    def _call_with_retry(
        self,
        provider: StructuredExtractionProvider | None,
        request: StructuredExtractionRequest,
    ) -> StructuredExtractionResult:
        if provider is None:
            return StructuredExtractionResult(
                state=ExtractionState.UNRESOLVED,
                reason_code="FAIL_CLOSED_NO_PROVIDER",
            )
        attempts = 1 + max(0, self.config.max_retries)
        last: StructuredExtractionResult | None = None
        for _ in range(attempts):
            last = provider.extract(request)
            # Technical retry only on provider/transport errors (same provider).
            if last.state is not ExtractionState.PROVIDER_ERROR:
                return last
        assert last is not None
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
            attempt=attempt,
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
