"""Application settings and profile selection."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from halyk_agent.profiles import ExecutionProfile, ProfileName, load_profile


class Settings(BaseSettings):
    """Process settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="HALYK_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    profile: ProfileName = Field(default=ProfileName.FAST)
    app_name: str = Field(default="halyk-agent")
    stage: int = Field(default=1, ge=1)
    postgres_dsn: str | None = Field(default=None)
    redis_url: str | None = Field(default=None)

    def execution_profile(self) -> ExecutionProfile:
        """Return the configured FAST or FULL profile declaration."""
        return load_profile(self.profile)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()


HealthStatus = Literal["ok"]
