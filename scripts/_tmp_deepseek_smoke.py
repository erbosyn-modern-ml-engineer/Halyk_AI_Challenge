"""Live DeepSeek smoke: one bounded TRANSACTION_RECLASSIFICATION extraction."""

from __future__ import annotations

from dotenv import load_dotenv

load_dotenv(".env", override=True)

from halyk_agent.config import get_settings
from halyk_agent.domain.models_gateway.budget import ExternalAttemptBudget
from halyk_agent.domain.models_gateway.gateway import LlmGatewayConfig, StructuredModelGateway
from halyk_agent.domain.models_gateway.types import StructuredExtractionRequest

get_settings.cache_clear()
settings = get_settings()

fragment = (
    "(11.1) Операция TXN-KC-CAP-16 ($162,670,254.82, Steppe Fibre Contractors) была запрошена "
    "кредитором к проверке; корректировка для целей ковенантов не требуется, и её "
    "первоначальная классификация сохраняется."
)

req = StructuredExtractionRequest(
    requirement_id="smoke-reclass-kc-cap16",
    scenario_id="KC",
    fact_kind="TRANSACTION_RECLASSIFICATION",
    authority_domain="FINANCIAL_ADJUSTMENTS",
    source_document_id="smoke-doc",
    source_sha256="a" * 64,
    window_hash="smoke-window-v1",
    fragments=[{"fragment_id": "F001", "text": fragment}],
    prompt_version="fact-extract-v1",
    schema_version="fact-payload-v1",
)

cfg = LlmGatewayConfig(
    primary_provider=settings.llm_primary_provider,
    primary_model=settings.llm_primary_model,
    escalation_provider=settings.llm_escalation_provider,
    escalation_model=settings.llm_escalation_model,
    timeout_seconds=settings.llm_timeout_seconds,
    max_external_attempts=3,
    max_thinking_escalations=0,
    max_concurrency=1,
    max_retries=settings.llm_max_retries,
    temperature=settings.llm_temperature,
    max_tokens=settings.llm_max_tokens,
    provider_revision=settings.llm_provider_revision,
    cache_dir=None,
    allow_network=True,
)
gw = StructuredModelGateway(config=cfg, shared_budget=ExternalAttemptBudget(max_attempts=3))
result, record = gw.extract(req)
usage = getattr(record, "usage", None) or result.usage
print("state", getattr(result.state, "value", result.state))
print("reason", result.reason_code)
print("model", record.model)
print("provider", getattr(record.provider, "value", record.provider))
print("cache_hit", record.cache_hit)
print("http_attempted", not record.cache_hit)
print("record_fields", sorted(type(record).model_fields))
if usage is not None:
    print("tokens_prompt", getattr(usage, "prompt_tokens", None))
    print("tokens_completion", getattr(usage, "completion_tokens", None))
    print("tokens_total", getattr(usage, "total_tokens", None))
print("has_payload", result.payload is not None)
if result.payload:
    print("txn", result.payload.get("transaction_id"))
    print("disp", result.payload.get("disposition"))
print("quote_ok", bool(result.quote) and (result.quote in fragment))
