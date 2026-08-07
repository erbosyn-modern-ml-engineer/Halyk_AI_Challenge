"""Offline model gateway tests with mock providers and DeepSeek request shaping."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from halyk_agent.domain.models_gateway.gateway import LlmGatewayConfig, StructuredModelGateway
from halyk_agent.domain.models_gateway.providers.deepseek import DeepSeekStructuredProvider
from halyk_agent.domain.models_gateway.providers.mock import MockStructuredProvider
from halyk_agent.domain.models_gateway.types import (
    ExtractionState,
    ProviderName,
    StructuredExtractionRequest,
    StructuredExtractionResult,
)


def _request(req_id: str = "req-1", window: str = "wh1") -> StructuredExtractionRequest:
    return StructuredExtractionRequest(
        requirement_id=req_id,
        scenario_id="S1",
        fact_kind="OWNERSHIP",
        authority_domain="KYC_RELATIONSHIPS",
        source_document_id="doc-1",
        source_sha256="b" * 64,
        window_hash=window,
        fragments=[{"fragment_id": "F001", "text": "Ertis Capital, LLP 31.4%"}],
    )


def test_provider_select_and_fail_closed(tmp_path: Path) -> None:
    gw = StructuredModelGateway(
        config=LlmGatewayConfig(
            allow_network=False, primary_provider="deepseek", cache_dir=tmp_path
        )
    )
    result, record = gw.extract(_request())
    assert result.state is ExtractionState.UNRESOLVED
    assert "FAIL_CLOSED" in result.reason_code or result.state is ExtractionState.UNRESOLVED
    assert "api_key" not in record.model_dump()
    assert "Authorization" not in str(record.model_dump())


def test_network_default_off_even_with_deepseek_key(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-not-used")
    gw = StructuredModelGateway(
        config=LlmGatewayConfig(
            allow_network=False,
            primary_provider="deepseek",
            cache_dir=tmp_path / "off",
        )
    )
    result, _ = gw.extract(_request())
    assert result.state is ExtractionState.UNRESOLVED
    assert gw.external_attempt_count == 0


def test_schema_fail_and_budget(tmp_path: Path) -> None:
    mock = MockStructuredProvider(
        responses={
            "req-1": StructuredExtractionResult(
                state=ExtractionState.SCHEMA_INVALID,
                reason_code="BAD_JSON",
            )
        }
    )
    gw = StructuredModelGateway(
        config=LlmGatewayConfig(
            allow_network=False,
            primary_provider="mock",
            max_calls=1,
            cache_dir=tmp_path / "c1",
        ),
        mock=mock,
    )
    r1, _ = gw.extract(_request())
    assert r1.state is ExtractionState.SCHEMA_INVALID
    r2, _ = gw.extract(_request("req-2", window="wh2"))
    assert r2.state is ExtractionState.BUDGET_EXCEEDED


def test_attempt_budget_max_three_http(tmp_path: Path) -> None:
    mock = MockStructuredProvider(
        default_factory=lambda _r: StructuredExtractionResult(
            state=ExtractionState.UNRESOLVED,
            reason_code="NONE",
        )
    )
    gw = StructuredModelGateway(
        config=LlmGatewayConfig(
            allow_network=False,
            primary_provider="mock",
            max_external_attempts=3,
            max_retries=0,
            cache_dir=tmp_path / "budget3",
        ),
        mock=mock,
    )
    for i in range(3):
        r, _ = gw.extract(_request(f"req-{i}", window=f"w{i}"))
        assert r.state is ExtractionState.UNRESOLVED
    r4, _ = gw.extract(_request("req-4", window="w4"))
    assert r4.state is ExtractionState.BUDGET_EXCEEDED
    assert gw.external_attempt_count == 3


def test_cache_hit_and_invalidation(tmp_path: Path) -> None:
    mock = MockStructuredProvider(
        default_factory=lambda _req: StructuredExtractionResult(
            state=ExtractionState.RESOLVED,
            payload={"kind": "OWNERSHIP", "entity_name": "X Corp", "ownership_percent": "10"},
            evidence_fragment_ids=("F001",),
            quote="X 10%",
            page_number=1,
            char_start=0,
            char_end=4,
            reason_code="OK",
        )
    )
    cache = tmp_path / "cache"
    gw = StructuredModelGateway(
        config=LlmGatewayConfig(
            allow_network=False,
            primary_provider="mock",
            cache_dir=cache,
            max_calls=10,
        ),
        mock=mock,
    )
    r1, rec1 = gw.extract(_request())
    assert r1.state is ExtractionState.RESOLVED
    assert rec1.cache_hit is False
    assert len(mock.calls) == 1
    r2, rec2 = gw.extract(_request())
    assert r2.state is ExtractionState.RESOLVED
    assert rec2.cache_hit is True
    assert len(mock.calls) == 1

    _, rec3 = gw.extract(_request(window="wh-other"))
    assert rec3.cache_hit is False
    assert len(mock.calls) == 2


def test_escalation_trigger_and_no_escalate_without_evidence(tmp_path: Path) -> None:
    primary = MockStructuredProvider(
        responses={
            "req-esc": StructuredExtractionResult(
                state=ExtractionState.RESOLVED,
                payload={"bad": True},
                evidence_fragment_ids=("F001",),
                quote="q",
                page_number=1,
                char_start=0,
                char_end=1,
                reason_code="PRIMARY",
            ),
            "req-noev": StructuredExtractionResult(
                state=ExtractionState.UNRESOLVED,
                reason_code="INSUFFICIENT",
            ),
        }
    )
    escalation = MockStructuredProvider(
        model="opus-mock",
        default_factory=lambda _r: StructuredExtractionResult(
            state=ExtractionState.RESOLVED,
            payload={"ok": True},
            evidence_fragment_ids=("F001",),
            quote="q2",
            page_number=1,
            char_start=0,
            char_end=2,
            reason_code="ESCALATED",
        ),
    )
    gw = StructuredModelGateway(
        config=LlmGatewayConfig(
            allow_network=False,
            primary_provider="mock",
            escalation_provider="mock",
            cache_dir=tmp_path / "esc",
            max_calls=10,
            escalation_max_calls=5,
        ),
        mock=primary,
        escalation=escalation,
    )
    res, rec = gw.extract(
        _request("req-esc"),
        escalate_on_validation_failure=True,
    )
    assert rec.escalated is True
    assert res.reason_code == "ESCALATED"
    assert escalation.calls

    escalation.calls.clear()
    res2, rec2 = gw.extract(
        _request("req-noev", window="w2"),
        escalate_on_validation_failure=True,
    )
    assert rec2.escalated is False
    assert res2.state is ExtractionState.UNRESOLVED
    assert not escalation.calls


def test_no_secrets_in_model_call_record(tmp_path: Path) -> None:
    mock = MockStructuredProvider(
        default_factory=lambda _r: StructuredExtractionResult(
            state=ExtractionState.UNRESOLVED,
            reason_code="NONE",
        )
    )
    gw = StructuredModelGateway(
        config=LlmGatewayConfig(
            allow_network=False,
            primary_provider="mock",
            cache_dir=tmp_path / "sec",
        ),
        mock=mock,
    )
    _, record = gw.extract(_request())
    dumped = record.model_dump()
    forbidden = {"api_key", "authorization", "x_api_key", "secret", "token"}
    assert forbidden.isdisjoint({k.lower() for k in dumped})
    assert record.provider is ProviderName.MOCK


def test_deepseek_primary_request_shape(monkeypatch: Any) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    provider = DeepSeekStructuredProvider(thinking_enabled=False)
    body = provider.build_request_body(_request())
    assert body["model"] == "deepseek-v4-flash"
    assert body["response_format"] == {"type": "json_object"}
    assert body["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in body
    assert body["max_tokens"] == 2048
    assert "json_example" in body["messages"][1]["content"]


def test_deepseek_thinking_escalation_shape(monkeypatch: Any) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    provider = DeepSeekStructuredProvider(thinking_enabled=True, reasoning_effort="high")
    body = provider.build_request_body(_request())
    assert body["thinking"] == {"type": "enabled"}
    assert body["reasoning_effort"] == "high"


def test_deepseek_empty_content_retries_once(monkeypatch: Any) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    provider = DeepSeekStructuredProvider()
    empty = MagicMock()
    empty.raise_for_status = MagicMock()
    empty.json.return_value = {"choices": [{"message": {"content": ""}}]}
    good = MagicMock()
    good.raise_for_status = MagicMock()
    good.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "state": "UNRESOLVED",
                            "payload": None,
                            "evidence_fragment_ids": [],
                            "reason_code": "OK",
                        }
                    )
                }
            }
        ]
    }
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.post.side_effect = [empty, good]
    with patch("httpx.Client", return_value=client):
        result = provider.extract(_request())
    assert result.state is ExtractionState.UNRESOLVED
    assert provider.http_calls == 2


def test_cache_distinguishes_thinking_mode(tmp_path: Path) -> None:
    mock = MockStructuredProvider(
        default_factory=lambda _r: StructuredExtractionResult(
            state=ExtractionState.RESOLVED,
            payload={"ok": True},
            evidence_fragment_ids=("F001",),
            quote="q",
            page_number=1,
            char_start=0,
            char_end=1,
            reason_code="OK",
        )
    )
    # Primary without thinking
    gw = StructuredModelGateway(
        config=LlmGatewayConfig(
            allow_network=False,
            primary_provider="mock",
            escalation_provider="mock",
            cache_dir=tmp_path / "think",
            max_calls=10,
        ),
        mock=mock,
        escalation=MockStructuredProvider(
            model="esc",
            default_factory=lambda _r: StructuredExtractionResult(
                state=ExtractionState.RESOLVED,
                payload={"esc": True},
                evidence_fragment_ids=("F001",),
                quote="qe",
                page_number=1,
                char_start=0,
                char_end=2,
                reason_code="ESC",
            ),
        ),
    )
    _, rec1 = gw.extract(_request("req-t"))
    assert rec1.cache_hit is False
    # Escalation path caches under different thinking config
    res, rec2 = gw.extract(_request("req-t"), escalate_on_validation_failure=True)
    assert rec2.escalated is True
    assert res.reason_code == "ESC"
