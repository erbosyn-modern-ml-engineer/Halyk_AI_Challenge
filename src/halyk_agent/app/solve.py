"""Application composition: preflight then competition solve (sanitized manifest only)."""

from __future__ import annotations

import tempfile
from pathlib import Path

from halyk_agent.preflight.models import SanitizedDatasetManifest
from halyk_agent.preflight.service import load_sanitized_manifest, run_preflight
from halyk_agent.solver.solve import solve_competition_from_manifest


def run_solve_from_manifest(
    manifest: SanitizedDatasetManifest | Path,
    output: Path,
    *,
    team: str | None = None,
    contact_email: str | None = None,
    model_name: str | None = None,
    require_complete: bool = False,
) -> dict[str, str]:
    """Invoke competition solver with a sanitized manifest only (no raw root)."""
    resolved = (
        manifest
        if isinstance(manifest, SanitizedDatasetManifest)
        else load_sanitized_manifest(Path(manifest))
    )
    return solve_competition_from_manifest(
        resolved,
        output,
        team=team,
        contact_email=contact_email,
        model_name=model_name,
        require_complete=require_complete,
    )


def run_solve(
    dataset: Path,
    output: Path,
    *,
    team: str | None = None,
    contact_email: str | None = None,
    model_name: str | None = None,
    require_complete: bool = False,
) -> dict[str, str]:
    """Compatibility composition root: preflight(raw) then solve(sanitized).

    The solver service itself never receives ``dataset`` as a root traversal input.
    """
    output_parent = output.resolve().parent
    output_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".halyk-preflight-", dir=str(output_parent)) as tmp:
        manifest = run_preflight(dataset, Path(tmp))
        return run_solve_from_manifest(
            manifest,
            output,
            team=team,
            contact_email=contact_email,
            model_name=model_name,
            require_complete=require_complete,
        )
