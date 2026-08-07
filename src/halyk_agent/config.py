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

    profile: ProfileName = Field(default=ProfileName.FULL)
    app_name: str = Field(default="halyk-agent")
    stage: int = Field(default=5, ge=1)
    mode: Literal["competition", "training"] = Field(default="competition")
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

    # Parser limits / routing (Stage 3)
    parser_backend: Literal["pypdf", "auto"] = Field(default="pypdf")
    parser_primary: Literal["pypdf"] = Field(default="pypdf")
    parser_fallback: Literal["none", "docling"] = Field(default="none")
    force_docling: bool = Field(default=False)
    docling_ocr_enabled: bool = Field(default=False)
    docling_table_structure_enabled: bool = Field(default=True)
    # Selective OCR (Stage 5A.4) — explicit backend only; no silent fallback/download
    ocr_backend: Literal["none", "tesseract_cli"] = Field(default="none")
    ocr_languages: str = Field(default="eng+rus+kaz")
    ocr_max_pages: int = Field(default=32, ge=1)
    ocr_timeout_seconds: float = Field(default=60.0, gt=0.0)
    ocr_render_scale: float = Field(default=2.0, gt=0.0)
    ocr_psm: int = Field(default=6, ge=0, le=13)
    max_pdf_pages: int = Field(default=500, ge=1)
    max_page_characters: int = Field(default=500_000, ge=1)
    max_document_characters: int = Field(default=5_000_000, ge=1)
    max_parser_warnings: int = Field(default=200, ge=1)

    # Quality thresholds
    quality_min_total_characters: int = Field(default=1, ge=0)
    quality_max_empty_page_ratio: float = Field(default=0.5, ge=0.0, le=1.0)
    quality_max_replacement_character_ratio: float = Field(default=0.05, ge=0.0, le=1.0)
    quality_min_alphanumeric_ratio: float = Field(default=0.2, ge=0.0, le=1.0)
    quality_max_control_character_count: int = Field(default=100, ge=0)
    quality_max_duplicate_line_ratio: float = Field(default=0.9, ge=0.0, le=1.0)
    quality_max_pages_without_text_ratio: float = Field(default=0.5, ge=0.0, le=1.0)

    def execution_profile(self) -> ExecutionProfile:
        """Return the configured FAST or FULL profile declaration."""
        return load_profile(self.profile)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()


HealthStatus = Literal["ok"]
