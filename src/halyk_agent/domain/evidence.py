"""Evidence span domain models."""

from __future__ import annotations

import math
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from halyk_agent.domain.common import NonEmptyStr

BBox = Annotated[tuple[float, float, float, float], Field(min_length=4, max_length=4)]


class EvidenceSpan(BaseModel):
    """A grounded quote locating an extracted value in a source document."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: NonEmptyStr
    source_file: NonEmptyStr
    document_id: NonEmptyStr
    document_version_id: NonEmptyStr
    page_number: int = Field(ge=1)
    quote: NonEmptyStr
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)
    bbox: BBox | None = None
    block_id: NonEmptyStr | None = None
    table_id: NonEmptyStr | None = None
    row_index: int | None = Field(default=None, ge=0)
    column_index: int | None = Field(default=None, ge=0)

    @field_validator("quote")
    @classmethod
    def _quote_must_be_non_empty_after_strip(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("quote must be non-empty after stripping")
        return stripped

    @field_validator("bbox")
    @classmethod
    def _bbox_must_be_four_finite_numbers(
        cls,
        value: tuple[float, float, float, float] | None,
    ) -> tuple[float, float, float, float] | None:
        if value is None:
            return None
        if len(value) != 4:
            raise ValueError("bbox must contain exactly four numbers")
        if any(not math.isfinite(coord) for coord in value):
            raise ValueError("bbox coordinates must be finite numbers")
        return value

    @model_validator(mode="after")
    def _validate_character_range(self) -> EvidenceSpan:
        start = self.char_start
        end = self.char_end
        if (start is None) ^ (end is None):
            raise ValueError("char_start and char_end must both exist or both be absent")
        if start is not None and end is not None and start >= end:
            raise ValueError("char_start must be less than char_end")
        return self
