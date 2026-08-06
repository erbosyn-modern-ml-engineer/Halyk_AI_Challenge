"""Competition solver package (must never import ``halyk_agent.training``)."""

from __future__ import annotations

from halyk_agent.solver.mode import SolverMode, get_solver_mode, require_competition_mode

__all__ = [
    "SolverMode",
    "get_solver_mode",
    "require_competition_mode",
]
