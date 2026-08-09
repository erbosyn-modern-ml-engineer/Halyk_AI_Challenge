from __future__ import annotations

import json

import httpx

from halyk_agent.config import Settings
from halyk_agent.domain.models_gateway.semantic_json import (
    SemanticJsonGateway,
    SemanticJsonState,
)


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


def test_semantic_gateway_caches_valid_json(monkeypatch, tmp_path) -> None:
    calls = 0

    def fake_post(*args, **kwargs):
        nonlocal calls
        calls += 1
        content = json.dumps({"category": "LABOR", "confidence": "HIGH"})
        return _FakeResponse({"choices": [{"message": {"content": content}}]})

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(httpx, "post", fake_post)
    settings = Settings(
        semantic_fallback_enabled=True,
        llm_max_external_attempts=1,
        llm_max_retries=0,
    )
    gateway = SemanticJsonGateway(settings=settings, cache_dir=tmp_path)
    kwargs = dict(
        task_id="txn:P1:T1",
        prompt_version="p1",
        schema_version="s1",
        source_sha256="a" * 64,
        system_prompt="Return JSON only",
        request_payload={"description": "зарплата"},
        max_tokens=128,
    )

    first = gateway.propose(**kwargs)
    second = gateway.propose(**kwargs)

    assert first.state is SemanticJsonState.RESOLVED
    assert first.payload == {"category": "LABOR", "confidence": "HIGH"}
    assert first.model_called is True
    assert second.state is SemanticJsonState.CACHE_HIT
    assert second.cache_hit is True
    assert second.model_called is False
    assert calls == 1
    assert gateway.external_attempt_count == 1


def test_semantic_gateway_fails_closed_without_key(monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    gateway = SemanticJsonGateway(settings=Settings(semantic_fallback_enabled=True))
    result = gateway.propose(
        task_id="x",
        prompt_version="p1",
        schema_version="s1",
        source_sha256="b" * 64,
        system_prompt="Return JSON only",
        request_payload={"x": 1},
        max_tokens=32,
    )
    assert result.state is SemanticJsonState.PROVIDER_ERROR
    assert result.reason_code == "MISSING_DEEPSEEK_API_KEY"
    assert result.model_called is False
