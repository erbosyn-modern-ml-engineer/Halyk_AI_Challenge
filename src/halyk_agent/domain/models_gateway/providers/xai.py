"""xAI / Grok OpenAI-compatible structured extraction provider."""

from __future__ import annotations

import json
import os
from typing import Any

from halyk_agent.domain.ids import sha256_text
from halyk_agent.domain.models_gateway.types import (
    ExtractionState,
    StructuredExtractionRequest,
    StructuredExtractionResult,
)

PROMPT_SYSTEM = (
    "Extract structured financial facts ONLY from the supplied evidence fragments. "
    "If evidence is insufficient, return state=UNRESOLVED. "
    "Never use outside knowledge. evidence_fragment_ids is mandatory when resolved. "
    "Respond with a single JSON object."
)


class XaiStructuredProvider:
    name = "xai"

    def __init__(
        self,
        *,
        model: str = "grok-4.5",
        api_key: str | None = None,
        base_url: str = "https://api.x.ai/v1",
        timeout_seconds: float = 60.0,
        temperature: float = 0.0,
    ) -> None:
        self.model = model
        # Read env at call-time if not provided; never log the key.
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._temperature = temperature

    def _resolve_key(self) -> str | None:
        return self._api_key or os.environ.get("XAI_API_KEY")

    def extract(
        self,
        request: StructuredExtractionRequest,
        *,
        budget: object | None = None,
    ) -> StructuredExtractionResult:
        _ = budget
        key = self._resolve_key()
        if not key:
            return StructuredExtractionResult(
                state=ExtractionState.PROVIDER_ERROR,
                reason_code="MISSING_XAI_API_KEY",
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
                "Return JSON with keys: state (RESOLVED|UNRESOLVED), payload (object|null), "
                "evidence_fragment_ids (array), quote, page_number, char_start, char_end, "
                "reason_code."
            ),
        }
        body: dict[str, Any] = {
            "model": self.model,
            "temperature": self._temperature,
            "messages": [
                {"role": "system", "content": PROMPT_SYSTEM},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_fact",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "state": {"type": "string"},
                            "payload": {"type": ["object", "null"]},
                            "evidence_fragment_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "quote": {"type": ["string", "null"]},
                            "page_number": {"type": ["integer", "null"]},
                            "char_start": {"type": ["integer", "null"]},
                            "char_end": {"type": ["integer", "null"]},
                            "reason_code": {"type": "string"},
                        },
                        "required": [
                            "state",
                            "payload",
                            "evidence_fragment_ids",
                            "reason_code",
                        ],
                    },
                },
            },
        }
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.post(
                    f"{self._base_url}/chat/completions",
                    headers=headers,
                    json=body,
                )
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except Exception as exc:
            return StructuredExtractionResult(
                state=ExtractionState.PROVIDER_ERROR,
                reason_code=f"XAI_HTTP_ERROR:{exc.__class__.__name__}",
            )

        return _parse_provider_json(parsed, raw_text=content if isinstance(content, str) else "")


def _parse_provider_json(parsed: dict[str, Any], *, raw_text: str) -> StructuredExtractionResult:
    state_raw = str(parsed.get("state", "UNRESOLVED")).upper()
    try:
        state = ExtractionState(state_raw)
    except ValueError:
        state = ExtractionState.SCHEMA_INVALID
    frag_ids = parsed.get("evidence_fragment_ids") or []
    if not isinstance(frag_ids, list):
        return StructuredExtractionResult(
            state=ExtractionState.SCHEMA_INVALID,
            reason_code="BAD_FRAGMENT_IDS",
        )
    return StructuredExtractionResult(
        state=state,
        payload=parsed.get("payload"),
        evidence_fragment_ids=tuple(str(x) for x in frag_ids),
        quote=parsed.get("quote"),
        page_number=parsed.get("page_number"),
        char_start=parsed.get("char_start"),
        char_end=parsed.get("char_end"),
        reason_code=str(parsed.get("reason_code") or "OK"),
        raw_response_hash=sha256_text(raw_text) if raw_text else None,
    )
