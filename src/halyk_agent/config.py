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
    stage: int = Field(default=2, ge=1)
    postgres_dsn: str | None = Field(default=None)
    redis_url: str | None = Field(default=None)

    # Archive safety / profiling limits (Stage 2)
    max_archive_files: int = Field(default=10_000, ge=1)
    max_single_file_bytes: int = Field(default=100_000_000, ge=1)
    max_total_uncompressed_bytes: int = Field(default=500_000_000, ge=1)
    max_compression_ratio: float = Field(default=100.0, gt=0.0)
    max_path_length: int = Field(default=512, ge=1)
    max_profile_file_bytes: int = Field(default=20_000_000, ge=1)
    max_sample_rows: int = Field(default=200, ge=1)
    max_sample_value_length: int = Field(default=200, ge=1)
    connector_batch_size: int = Field(default=50, ge=1)

    def execution_profile(self) -> ExecutionProfile:
        """Return the configured FAST or FULL profile declaration."""
        return load_profile(self.profile)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()


HealthStatus = Literal["ok"]
