"""DeepSeek V4 Flash structured extraction provider (primary Stage 5E runtime)."""

# ruff: noqa: RUF001

from __future__ import annotations

import json
import os
import time
from typing import Any

from halyk_agent.domain.fact_extraction.constants import DEFAULT_DEEPSEEK_MAX_TOKENS
from halyk_agent.domain.ids import sha256_text
from halyk_agent.domain.models_gateway.budget import BudgetExhaustedError, ExternalAttemptBudget
from halyk_agent.domain.models_gateway.types import (
    ExtractionState,
    StructuredExtractionRequest,
    StructuredExtractionResult,
    TokenUsage,
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
        "payload fields: transaction_id?, counterparty?, amount?, from_category?, to_category?, "
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

_FACT_JSON_EXAMPLES: dict[str, dict[str, Any]] = {
    "FX_RATE": {
        "state": "RESOLVED",
        "payload": {
            "kind": "FX_RATE",
            "from_currency": "EUR",
            "to_currency": "USD",
            "source_amount": {"value": "100", "currency": "EUR"},
            "settlement_amount": {"value": "116.00", "currency": "USD"},
            "explicit_rate": None,
            "rate_source": "NOT_STATED",
        },
        "evidence_fragment_ids": ["F001"],
        "quote": "Счёт на сумму 100 EUR урегулирован в размере $116.00",
        "page_number": 1,
        "char_start": 0,
        "char_end": 48,
        "reason_code": "OK",
    },
    "OWNERSHIP": {
        "state": "RESOLVED",
        "payload": {
            "kind": "OWNERSHIP",
            "entity_name": "Northbridge Infrastructure LLP",
            "ownership_percent": "37.5",
        },
        "evidence_fragment_ids": ["F001"],
        "quote": '"Northbridge Infrastructure" LLP 37.5%',
        "page_number": 1,
        "char_start": 0,
        "char_end": 40,
        "reason_code": "OK",
    },
    "TRANSACTION_PERIOD": {
        "state": "RESOLVED",
        "payload": {
            "kind": "TRANSACTION_PERIOD",
            "transaction_id": "TXN-X-0001",
            "disposition": "ASSIGN_TO_PERIOD",
            "service_start": "2026-01-15",
            "service_end": "2026-03-20",
        },
        "evidence_fragment_ids": ["F001"],
        "quote": "TXN-X-0001 относится к услугам с 2026-01-15 по 2026-03-20",
        "page_number": 1,
        "char_start": 0,
        "char_end": 55,
        "reason_code": "OK",
    },
    "TRANSACTION_RECLASSIFICATION": {
        "state": "RESOLVED",
        "payload": {
            "kind": "TRANSACTION_RECLASSIFICATION",
            "transaction_id": "TXN-X-0012",
            "amount": {"value": "25000.00", "currency": "USD"},
            "from_category": "Operating Expenses",
            "to_category": "Insurance Premiums",
            "disposition": "REJECTED",
        },
        "evidence_fragment_ids": ["F001"],
        "quote": "TXN-X-0012 considered for reclassification; original classification remains",
        "page_number": 1,
        "char_start": 0,
        "char_end": 70,
        "reason_code": "OK",
    },
    "AMOUNT_CORRECTION": {
        "state": "RESOLVED",
        "payload": {
            "kind": "AMOUNT_CORRECTION",
            "transaction_id": "TXN-X-0031",
            "amount": {"value": "12500.00", "currency": "USD"},
        },
        "evidence_fragment_ids": ["F001"],
        "quote": "TXN-X-0031 фактическая сумма составляет $12,500.00",
        "page_number": 1,
        "char_start": 0,
        "char_end": 50,
        "reason_code": "OK",
    },
    "OFF_LEDGER_AMOUNT": {
        "state": "RESOLVED",
        "payload": {
            "kind": "OFF_LEDGER_AMOUNT",
            "label": "severance_liability",
            "amount": {"value": "1000.00", "currency": "USD"},
        },
        "evidence_fragment_ids": ["F001"],
        "quote": "severance liability of $1,000.00",
        "page_number": 1,
        "char_start": 0,
        "char_end": 30,
        "reason_code": "OK",
    },
    "RELATED_PARTY_THRESHOLD": {
        "state": "RESOLVED",
        "payload": {
            "kind": "RELATED_PARTY_THRESHOLD",
            "threshold_percent": "20.0",
        },
        "evidence_fragment_ids": ["F001"],
        "quote": "владеет 20.0% и более ... связанными сторонами",
        "page_number": 1,
        "char_start": 0,
        "char_end": 45,
        "reason_code": "OK",
    },
    "SUBSIDIARY_STATUS": {
        "state": "RESOLVED",
        "payload": {
            "kind": "SUBSIDIARY_STATUS",
            "entity_name": "Example Sub LLP",
            "status": "UNRESTRICTED",
        },
        "evidence_fragment_ids": ["F001"],
        "quote": "Example Sub LLP is an unrestricted subsidiary",
        "page_number": 1,
        "char_start": 0,
        "char_end": 45,
        "reason_code": "OK",
    },
    "ONE_TIME_ADD_BACK": {
        "state": "RESOLVED",
        "payload": {
            "kind": "ONE_TIME_ADD_BACK",
            "label": "one_time_add_back",
            "amount": {"value": "5000.00", "currency": "USD"},
        },
        "evidence_fragment_ids": ["F001"],
        "quote": "one-time add-back of $5,000.00",
        "page_number": 1,
        "char_start": 0,
        "char_end": 30,
        "reason_code": "OK",
    },
    "TRANSACTION_TREATMENT": {
        "state": "RESOLVED",
        "payload": {
            "kind": "TRANSACTION_TREATMENT",
            "transaction_id": "TXN-X-0002",
            "disposition": "EXCLUDE",
        },
        "evidence_fragment_ids": ["F001"],
        "quote": "TXN-X-0002 исключается из расчёта ковенант",
        "page_number": 1,
        "char_start": 0,
        "char_end": 40,
        "reason_code": "OK",
    },
}

PROMPT_SYSTEM = (
    "Extract structured financial facts ONLY from the supplied evidence fragments. "
    "If evidence is insufficient, return state=UNRESOLVED. "
    "Never use outside knowledge. Never calculate unstated rates or values. "
    "evidence_fragment_ids is mandatory when resolved. "
    "quote MUST be an exact substring of one of the supplied fragment texts. "
    "Respond with a single JSON object matching the example shape. "
    "Do not include reasoning_content."
)


def _parse_usage(raw: Any) -> TokenUsage | None:
    if not isinstance(raw, dict):
        return None
    details = raw.get("completion_tokens_details") or raw.get("output_tokens_details") or {}
    prompt_details = raw.get("prompt_tokens_details") or raw.get("input_tokens_details") or {}

    def _int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None

    usage = TokenUsage(
        prompt_tokens=_int(raw.get("prompt_tokens") or raw.get("input_tokens")),
        completion_tokens=_int(raw.get("completion_tokens") or raw.get("output_tokens")),
        total_tokens=_int(raw.get("total_tokens")),
        prompt_cache_hit_tokens=_int(
            raw.get("prompt_cache_hit_tokens")
            or prompt_details.get("cached_tokens")
            or prompt_details.get("cache_hit_tokens")
        ),
        prompt_cache_miss_tokens=_int(
            raw.get("prompt_cache_miss_tokens") or prompt_details.get("cache_miss_tokens")
        ),
        reasoning_tokens=_int(
            raw.get("reasoning_tokens")
            or details.get("reasoning_tokens")
            or details.get("reasoning")
        ),
    )
    if all(
        getattr(usage, field) is None
        for field in (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "prompt_cache_hit_tokens",
            "prompt_cache_miss_tokens",
            "reasoning_tokens",
        )
    ):
        return None
    return usage


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
        max_tokens: int = DEFAULT_DEEPSEEK_MAX_TOKENS,
        budget: ExternalAttemptBudget | None = None,
    ) -> None:
        self.model = model
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._temperature = temperature
        self.thinking_enabled = thinking_enabled
        self.reasoning_effort = reasoning_effort
        self.max_tokens = max_tokens
        self.budget = budget
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

    def json_example_for(self, fact_kind: str) -> dict[str, Any]:
        return _FACT_JSON_EXAMPLES.get(
            fact_kind,
            {
                "state": "UNRESOLVED",
                "payload": None,
                "evidence_fragment_ids": [],
                "quote": None,
                "page_number": None,
                "char_start": None,
                "char_end": None,
                "reason_code": "INSUFFICIENT",
            },
        )

    def build_request_body(self, request: StructuredExtractionRequest) -> dict[str, Any]:
        contract = _FACT_CONTRACTS.get(
            request.fact_kind,
            "Return a payload object matching the fact kind schema.",
        )
        example = self.json_example_for(request.fact_kind)
        user_payload = {
            "requirement_id": request.requirement_id,
            "fact_kind": request.fact_kind,
            "authority_domain": request.authority_domain,
            "lexical_cues": list(request.lexical_cues),
            "fragments": list(request.fragments),
            "fact_contract": contract,
            "json_example": example,
            "instructions": (
                "Return JSON with keys: state (RESOLVED|UNRESOLVED), payload (object|null), "
                "evidence_fragment_ids (array), quote, page_number, char_start, char_end, "
                "reason_code. Follow the json_example shape for this fact_kind. "
                "Never invent FX rates or numeric values absent from fragments."
            ),
        }
        body: dict[str, Any] = {
            "model": self.model,
            "temperature": self._temperature,
            "max_tokens": self.max_tokens,
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

    def extract(
        self,
        request: StructuredExtractionRequest,
        *,
        budget: ExternalAttemptBudget | None = None,
    ) -> StructuredExtractionResult:
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

        active_budget = budget if budget is not None else self.budget
        body = self.build_request_body(request)
        self.last_request_body = body
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

        # At most one retry on empty content — each attempt claims its own permit.
        last_error = "DEEPSEEK_EMPTY_CONTENT"
        last_usage: TokenUsage | None = None
        started = time.perf_counter()
        for attempt in range(2):
            if active_budget is not None and not active_budget.try_claim():
                return StructuredExtractionResult(
                    state=ExtractionState.BUDGET_EXCEEDED,
                    reason_code="MAX_EXTERNAL_ATTEMPTS",
                    usage=last_usage,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
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
            except BudgetExhaustedError:
                return StructuredExtractionResult(
                    state=ExtractionState.BUDGET_EXCEEDED,
                    reason_code="MAX_EXTERNAL_ATTEMPTS",
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
            except Exception as exc:
                return StructuredExtractionResult(
                    state=ExtractionState.PROVIDER_ERROR,
                    reason_code=f"DEEPSEEK_HTTP_ERROR:{exc.__class__.__name__}",
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )

            last_usage = _parse_usage(data.get("usage"))
            message = (data.get("choices") or [{}])[0].get("message") or {}
            # Never persist reasoning_content — drop it immediately.
            content = message.get("content")
            latency_ms = int((time.perf_counter() - started) * 1000)
            if isinstance(content, str) and content.strip():
                try:
                    parsed = json.loads(content)
                except json.JSONDecodeError:
                    return StructuredExtractionResult(
                        state=ExtractionState.SCHEMA_INVALID,
                        reason_code="DEEPSEEK_BAD_JSON",
                        raw_response_hash=sha256_text(content),
                        usage=last_usage,
                        latency_ms=latency_ms,
                    )
                if not isinstance(parsed, dict):
                    return StructuredExtractionResult(
                        state=ExtractionState.SCHEMA_INVALID,
                        reason_code="DEEPSEEK_NON_OBJECT",
                        raw_response_hash=sha256_text(content),
                        usage=last_usage,
                        latency_ms=latency_ms,
                    )
                result = _parse_provider_json(parsed, raw_text=content)
                return result.model_copy(update={"usage": last_usage, "latency_ms": latency_ms})
            last_error = "DEEPSEEK_EMPTY_CONTENT"
            if attempt == 0:
                continue
            break

        return StructuredExtractionResult(
            state=ExtractionState.PROVIDER_ERROR,
            reason_code=last_error,
            usage=last_usage,
            latency_ms=int((time.perf_counter() - started) * 1000),
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
