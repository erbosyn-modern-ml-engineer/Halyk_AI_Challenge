"""DeepSeek V4 Flash structured extraction provider (primary Stage 5E runtime)."""

# ruff: noqa: RUF001

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

BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"

_FACT_CONTRACTS: dict[str, str] = {
    "FX_RATE": (
        "payload fields: from_currency, to_currency, source_amount?, settlement_amount?, "
        "explicit_rate (number|null), rate_source (EXPLICIT|NOT_STATED), transaction_id?. "
        "NEVER calculate or invent an unstated FX rate. If only invoice+settlement amounts "
        "appear without a stated rate, set explicit_rate=null and rate_source=NOT_STATED."
    ),
    "OWNERSHIP": (
        "payload fields: entity_name, ownership_percent, holder_label?, voting_rights?. "
        "entity_name must be a meaningful name, not only a legal form (LLP/JSC/ТОО/АО)."
    ),
    "TRANSACTION_PERIOD": (
        "payload fields: transaction_id, disposition (EXCLUDE_FROM_PERIOD|ASSIGN_TO_PERIOD), "
        "period_label?, service_start?, service_end?. Preserve ISO dates when present."
    ),
    "TRANSACTION_RECLASSIFICATION": (
        "payload fields: transaction_id?, counterparty?, amount?, from_category, to_category, "
        "disposition (ACCEPTED|REJECTED). Do not invent positive facts from absences."
    ),
    "AMOUNT_CORRECTION": (
        "payload fields: transaction_id?, amount {value,currency}, description?. "
        "Never invent amounts not present in fragments."
    ),
    "OFF_LEDGER_AMOUNT": "payload fields: label, amount {value,currency}, as_of_date?.",
    "RELATED_PARTY_THRESHOLD": "payload fields: threshold_percent, holder_label?.",
    "SUBSIDIARY_STATUS": (
        "payload fields: entity_name, status (RESTRICTED|UNRESTRICTED|GROUP_MEMBER)."
    ),
    "ONE_TIME_ADD_BACK": "payload fields: label, amount {value,currency}, materiality_note?.",
    "TRANSACTION_TREATMENT": (
        "payload fields: transaction_id, disposition (INCLUDE|EXCLUDE), reason?."
    ),
}

PROMPT_SYSTEM = (
    "Extract structured financial facts ONLY from the supplied evidence fragments. "
    "If evidence is insufficient, return state=UNRESOLVED. "
    "Never use outside knowledge. Never calculate unstated rates or values. "
    "evidence_fragment_ids is mandatory when resolved. "
    "quote MUST be an exact substring of one of the supplied fragment texts. "
    "Respond with a single JSON object. Do not include reasoning_content."
)


class DeepSeekStructuredProvider:
    name = "deepseek"

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        base_url: str = BASE_URL,
        timeout_seconds: float = 60.0,
        temperature: float = 0.0,
        thinking_enabled: bool = False,
        reasoning_effort: str | None = None,
    ) -> None:
        self.model = model
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._temperature = temperature
        self.thinking_enabled = thinking_enabled
        self.reasoning_effort = reasoning_effort
        self.last_request_body: dict[str, Any] | None = None
        self.http_calls = 0

    def _resolve_key(self) -> str | None:
        return self._api_key or os.environ.get("DEEPSEEK_API_KEY")

    def thinking_config(self) -> dict[str, Any]:
        cfg: dict[str, Any] = {
            "thinking": {"type": "enabled" if self.thinking_enabled else "disabled"},
        }
        if self.thinking_enabled and self.reasoning_effort:
            cfg["reasoning_effort"] = self.reasoning_effort
        return cfg

    def build_request_body(self, request: StructuredExtractionRequest) -> dict[str, Any]:
        contract = _FACT_CONTRACTS.get(
            request.fact_kind,
            "Return a payload object matching the fact kind schema.",
        )
        user_payload = {
            "requirement_id": request.requirement_id,
            "fact_kind": request.fact_kind,
            "authority_domain": request.authority_domain,
            "lexical_cues": list(request.lexical_cues),
            "fragments": list(request.fragments),
            "fact_contract": contract,
            "instructions": (
                "Return JSON with keys: state (RESOLVED|UNRESOLVED), payload (object|null), "
                "evidence_fragment_ids (array), quote, page_number, char_start, char_end, "
                "reason_code. Never invent FX rates or numeric values absent from fragments."
            ),
        }
        body: dict[str, Any] = {
            "model": self.model,
            "temperature": self._temperature,
            "messages": [
                {"role": "system", "content": PROMPT_SYSTEM},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
            "thinking": {"type": "enabled" if self.thinking_enabled else "disabled"},
        }
        if self.thinking_enabled and self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        return body

    def extract(self, request: StructuredExtractionRequest) -> StructuredExtractionResult:
        key = self._resolve_key()
        if not key:
            return StructuredExtractionResult(
                state=ExtractionState.PROVIDER_ERROR,
                reason_code="MISSING_DEEPSEEK_API_KEY",
            )
        try:
            import httpx
        except ImportError:
            return StructuredExtractionResult(
                state=ExtractionState.PROVIDER_ERROR,
                reason_code="HTTPX_MISSING",
            )

        body = self.build_request_body(request)
        self.last_request_body = body
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

        # At most one retry on empty content.
        last_error = "DEEPSEEK_EMPTY_CONTENT"
        for attempt in range(2):
            try:
                self.http_calls += 1
                with httpx.Client(timeout=self._timeout) as client:
                    response = client.post(
                        f"{self._base_url}/chat/completions",
                        headers=headers,
                        json=body,
                    )
                response.raise_for_status()
                data = response.json()
            except Exception as exc:
                return StructuredExtractionResult(
                    state=ExtractionState.PROVIDER_ERROR,
                    reason_code=f"DEEPSEEK_HTTP_ERROR:{exc.__class__.__name__}",
                )

            message = (data.get("choices") or [{}])[0].get("message") or {}
            # Never persist reasoning_content — drop it immediately.
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                try:
                    parsed = json.loads(content)
                except json.JSONDecodeError:
                    return StructuredExtractionResult(
                        state=ExtractionState.SCHEMA_INVALID,
                        reason_code="DEEPSEEK_BAD_JSON",
                        raw_response_hash=sha256_text(content),
                    )
                if not isinstance(parsed, dict):
                    return StructuredExtractionResult(
                        state=ExtractionState.SCHEMA_INVALID,
                        reason_code="DEEPSEEK_NON_OBJECT",
                        raw_response_hash=sha256_text(content),
                    )
                return _parse_provider_json(parsed, raw_text=content)
            last_error = "DEEPSEEK_EMPTY_CONTENT"
            if attempt == 0:
                continue
            break

        return StructuredExtractionResult(
            state=ExtractionState.PROVIDER_ERROR,
            reason_code=last_error,
        )


def _parse_provider_json(parsed: dict[str, Any], *, raw_text: str) -> StructuredExtractionResult:
    # Strip any accidental reasoning fields from parsed payload persistence path.
    parsed.pop("reasoning_content", None)
    parsed.pop("reasoning", None)
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
