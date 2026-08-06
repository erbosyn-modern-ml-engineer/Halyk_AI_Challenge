"""Training scorer report models."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class CellScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    covenant_id: str
    cell_score: Decimal
    status_ok: bool
    relative_error: Decimal | None
    actual_component: Decimal
    evidence_component: Decimal
    notes: str = ""


class ScoreReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uniform_total: Decimal
    uniform_mean_cell: Decimal
    weighted_total: Decimal | None = None
    official_weights_known: bool = False
    weights_label: str = "unknown_official_weights"
    cell_count: int
    cells: list[CellScore] = Field(default_factory=list)
    missing_cells: list[str] = Field(default_factory=list)
    extra_cells: list[str] = Field(default_factory=list)
    malformed_cells: list[str] = Field(default_factory=list)
