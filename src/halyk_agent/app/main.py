"""Minimal FastAPI composition root for Stage 1."""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict

from halyk_agent.config import Settings, get_settings
from halyk_agent.profiles import ProfileName


class HealthResponse(BaseModel):
    """Health endpoint payload."""

    model_config = ConfigDict(extra="forbid")

    status: str
    stage: int
    profile: ProfileName


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the Stage 1 API application without external service wiring."""
    resolved = settings or get_settings()
    app = FastAPI(title=resolved.app_name, version="0.1.0")

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            stage=resolved.stage,
            profile=resolved.profile,
        )

    return app


app = create_app()
