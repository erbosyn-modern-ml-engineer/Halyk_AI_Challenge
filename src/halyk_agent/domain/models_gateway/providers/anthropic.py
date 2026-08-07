"""Optional Anthropic escalation provider for structured extraction."""

from __future__ import annotations

import json
import os
from typing import Any

from halyk_agent.domain.models_gateway.providers.xai import PROMPT_SYSTEM, _parse_provider_json
from halyk_agent.domain.models_gateway.types import (
    ExtractionState,
    StructuredExtractionRequest,
    StructuredExtractionResult,
)


class AnthropicStructuredProvider:
    name = "anthropic"

    def __init__(
        self,
        *,
        model: str = "claude-opus-5",
        api_key: str | None = None,
        timeout_seconds: float = 60.0,
        temperature: float = 0.0,
    ) -> None:
        self.model = model
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._temperature = temperature

    def _resolve_key(self) -> str | None:
        return self._api_key or os.environ.get("ANTHROPIC_API_KEY")

    def extract(self, request: StructuredExtractionRequest) -> StructuredExtractionResult:
        key = self._resolve_key()
        if not key:
            return StructuredExtractionResult(
                state=ExtractionState.PROVIDER_ERROR,
                reason_code="MISSING_ANTHROPIC_API_KEY",
            )
        try:
            import httpx
        except ImportError:
            return StructuredExtractionResult(
                state=ExtractionState.PROVIDER_ERROR,
                reason_code="HTTPX_MISSING",
            )

        user_payload = {
            "requirement_id": request.requirement_id,
            "fact_kind": request.fact_kind,
            "authority_domain": request.authority_domain,
            "lexical_cues": list(request.lexical_cues),
            "fragments": list(request.fragments),
            "instructions": (
                "Return ONLY JSON with keys: state, payload, evidence_fragment_ids, "
                "quote, page_number, char_start, char_end, reason_code."
            ),
        }
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 2048,
            "temperature": self._temperature,
            "system": PROMPT_SYSTEM,
            "messages": [
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False),
                }
            ],
        }
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers=headers,
                    json=body,
                )
            response.raise_for_status()
            data = response.json()
            blocks = data.get("content") or []
            content = ""
            for block in blocks:
                if isinstance(block, dict) and block.get("type") == "text":
                    content += str(block.get("text") or "")
            parsed = json.loads(content)
        except Exception as exc:
            return StructuredExtractionResult(
                state=ExtractionState.PROVIDER_ERROR,
                reason_code=f"ANTHROPIC_HTTP_ERROR:{exc.__class__.__name__}",
            )

        return _parse_provider_json(parsed, raw_text=content)
