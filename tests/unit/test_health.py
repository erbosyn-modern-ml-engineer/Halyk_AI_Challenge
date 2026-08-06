"""FastAPI health endpoint smoke test."""

from __future__ import annotations

from fastapi.testclient import TestClient

from halyk_agent.app.main import create_app
from halyk_agent.config import Settings
from halyk_agent.profiles import ProfileName


def test_health_endpoint_reports_stage_and_profile() -> None:
    settings = Settings(profile=ProfileName.FULL, stage=5, app_name="halyk-agent")
    client = TestClient(create_app(settings))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "stage": 5, "profile": "full"}
