"""Runtime mode gates for competition and training execution."""

from __future__ import annotations

import os
from enum import StrEnum


class SolverMode(StrEnum):
    COMPETITION = "competition"
    TRAINING = "training"


class CompetitionSolveMode(StrEnum):
    SOURCE_STRICT = "source-strict"
    COMPETITIVE_BOUNDED_V1 = "competitive-bounded-v1"


class ModeError(RuntimeError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def get_solver_mode() -> SolverMode:
    raw = (os.environ.get("HALYK_MODE") or "competition").strip().lower()
    if raw in {"competition", "comp"}:
        return SolverMode.COMPETITION
    if raw in {"training", "train"}:
        return SolverMode.TRAINING
    raise ModeError(f"invalid HALYK_MODE={raw!r}; expected competition|training")


def configured_competition_solve_mode() -> CompetitionSolveMode:
    raw = (os.environ.get("HALYK_COMPETITION_SOLVE_MODE") or "source-strict").strip().lower()
    try:
        return CompetitionSolveMode(raw)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in CompetitionSolveMode)
        raise ModeError(
            f"invalid HALYK_COMPETITION_SOLVE_MODE={raw!r}; expected one of: {allowed}"
        ) from exc


def solve_mode_manifest(mode: CompetitionSolveMode) -> dict[str, object]:
    return {
        "schema_version": "halyk.competition_solve_mode.v1",
        "mode": mode.value,
        "strict_stage6_only": mode is CompetitionSolveMode.SOURCE_STRICT,
        "competitive_fallback_policy_version": (
            "bounded-v1" if mode is CompetitionSolveMode.COMPETITIVE_BOUNDED_V1 else None
        ),
    }


def require_competition_mode() -> None:
    mode = get_solver_mode()
    if mode is not SolverMode.COMPETITION:
        raise ModeError(f"requires HALYK_MODE=competition (current={mode.value})")


def require_training_mode() -> None:
    mode = get_solver_mode()
    if mode is not SolverMode.TRAINING:
        raise ModeError(f"requires HALYK_MODE=training (current={mode.value})")
