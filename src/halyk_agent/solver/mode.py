"""Competition vs training mode gate."""

from __future__ import annotations

import os
from enum import StrEnum


class SolverMode(StrEnum):
    COMPETITION = "competition"
    TRAINING = "training"


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


def require_competition_mode() -> None:
    mode = get_solver_mode()
    if mode is not SolverMode.COMPETITION:
        raise ModeError(f"requires HALYK_MODE=competition (current={mode.value})")


def require_training_mode() -> None:
    mode = get_solver_mode()
    if mode is not SolverMode.TRAINING:
        raise ModeError(f"requires HALYK_MODE=training (current={mode.value})")
