"""Bounded JSON-only semantic gateway for non-arithmetic LLM proposals.

This gateway is intentionally narrower than the Stage 5E fact gateway.  It is
used only when deterministic logic has already returned UNRESOLVED/UNKNOWN and
only returns an untrusted JSON candidate.  Domain-specific callers remain
responsible for enum/type validation, exact source grounding, conflict checks,
and all financial arithmetic.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from halyk_agent.config import Settings
from halyk_agent.domain.ids import deterministic_id, sha256_text
from halyk_agent.domain.models_gateway.budget import ExternalAttemptBudget

SEMANTIC_GATEWAY_VERSION = "semantic-json-v1"


class SemanticJsonState(StrEnum):
    RESOLVED = "RESOLVED"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    DISABLED = "DISABLED"
    CACHE_HIT = "CACHE_HIT"


class SemanticJsonResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: SemanticJsonState
    payload: dict[str, Any] | None = None
    reason_code: str
    raw_response_hash: str | None = None
    model_called: bool = False
    cache_hit: bool = False


@dataclass
class SemanticJsonGateway:
    """Shared fail-closed gateway for typed semantic candidates.

    The cache identity binds provider/model revision, prompt/schema versions,
    source hash and the complete request payload.  A permit is claimed before
    every actual HTTP attempt, including retries.
    """

    settings: Settings
    cache_dir: Path | None = None
    budget: ExternalAttemptBudget | None = None
    _sem: threading.Semaphore = field(init=False)

    def __post_init__(self) -> None:
        self._sem = threading.Semaphore(max(1, self.settings.llm_max_concurrency))
        if self.budget is None:
            self.budget = ExternalAttemptBudget(self.settings.llm_max_external_attempts)

    @property
    def external_attempt_count(self) -> int:
        assert self.budget is not None
        return self.budget.used

    def propose(
        self,
        *,
        task_id: str,
        prompt_version: str,
        schema_version: str,
        source_sha256: str,
        system_prompt: str,
        request_payload: dict[str, Any],
        max_tokens: int,
    ) -> SemanticJsonResult:
        if not self.settings.semantic_fallback_enabled:
            return SemanticJsonResult(
                state=SemanticJsonState.DISABLED,
                reason_code="SEMANTIC_FALLBACK_DISABLED",
            )
        if self.settings.llm_primary_provider.casefold() != "deepseek":
            return SemanticJsonResult(
                state=SemanticJsonState.PROVIDER_ERROR,
                reason_code="PRIMARY_PROVIDER_NOT_DEEPSEEK",
            )
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            return SemanticJsonResult(
                state=SemanticJsonState.PROVIDER_ERROR,
                reason_code="MISSING_DEEPSEEK_API_KEY",
            )

        request_hash = sha256_text(
            json.dumps(request_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
        key = deterministic_id(
            SEMANTIC_GATEWAY_VERSION,
            self.settings.llm_primary_provider.casefold(),
            self.settings.llm_primary_model,
            self.settings.llm_provider_revision,
            prompt_version,
            schema_version,
            task_id,
            source_sha256,
            request_hash,
            sha256_text(system_prompt),
            str(min(max_tokens, self.settings.llm_max_tokens)),
        )
        cached = self._cache_get(key)
        if cached is not None:
            return cached.model_copy(
                update={
                    "state": SemanticJsonState.CACHE_HIT,
                    "model_called": False,
                    "cache_hit": True,
                }
            )

        try:
            import httpx
        except ImportError:
            return SemanticJsonResult(
                state=SemanticJsonState.PROVIDER_ERROR,
                reason_code="HTTPX_MISSING",
            )

        body = {
            "model": self.settings.llm_primary_model,
            "temperature": 0.0,
            "max_tokens": min(max_tokens, self.settings.llm_max_tokens),
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(request_payload, ensure_ascii=False, sort_keys=True),
                },
            ],
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
        }

        last_reason = "PROVIDER_ERROR"
        attempts = max(1, self.settings.llm_max_retries + 1)
        for _ in range(attempts):
            assert self.budget is not None
            if not self.budget.try_claim():
                return SemanticJsonResult(
                    state=SemanticJsonState.BUDGET_EXCEEDED,
                    reason_code="MAX_EXTERNAL_ATTEMPTS",
                    model_called=False,
                )
            try:
                with self._sem:
                    response = httpx.post(
                        "https://api.deepseek.com/chat/completions",
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                        json=body,
                        timeout=self.settings.llm_timeout_seconds,
                    )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                parsed = json.loads(content)
                if not isinstance(parsed, dict):
                    last_reason = "RESPONSE_NOT_JSON_OBJECT"
                    continue
                result = SemanticJsonResult(
                    state=SemanticJsonState.RESOLVED,
                    payload=parsed,
                    reason_code="OK",
                    raw_response_hash=sha256_text(content),
                    model_called=True,
                )
                self._cache_put(key, result)
                return result
            except (
                httpx.HTTPError,
                KeyError,
                IndexError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
            ) as exc:
                last_reason = f"PROVIDER_OR_JSON_ERROR:{exc.__class__.__name__}"

        return SemanticJsonResult(
            state=SemanticJsonState.SCHEMA_INVALID
            if "JSON" in last_reason
            else SemanticJsonState.PROVIDER_ERROR,
            reason_code=last_reason,
            model_called=True,
        )

    def _cache_path(self, key: str) -> Path | None:
        if self.cache_dir is None:
            return None
        return self.cache_dir / f"{key}.json"

    def _cache_get(self, key: str) -> SemanticJsonResult | None:
        path = self._cache_path(key)
        if path is None or not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if raw.get("gateway_version") != SEMANTIC_GATEWAY_VERSION:
                return None
            return SemanticJsonResult.model_validate(raw.get("result"))
        except (OSError, ValueError, TypeError):
            return None

    def _cache_put(self, key: str, result: SemanticJsonResult) -> None:
        path = self._cache_path(key)
        if path is None:
            return
        payload = {
            "gateway_version": SEMANTIC_GATEWAY_VERSION,
            "result": result.model_dump(mode="json"),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        tmp.replace(path)
