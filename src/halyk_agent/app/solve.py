"""Application wrapper for competition solve."""

from __future__ import annotations

from pathlib import Path

from halyk_agent.solver.solve import solve_dataset


def run_solve(
    dataset: Path,
    output: Path,
    *,
    team: str | None = None,
    contact_email: str | None = None,
    model_name: str | None = None,
) -> dict[str, str]:
    return solve_dataset(
        dataset,
        output,
        team=team,
        contact_email=contact_email,
        model_name=model_name,
    )
