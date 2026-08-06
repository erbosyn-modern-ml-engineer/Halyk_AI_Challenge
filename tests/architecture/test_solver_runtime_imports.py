"""Runtime import isolation: solver must not load preflight discovery/quarantine."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_solver_import_does_not_load_preflight_implementations() -> None:
    code = r"""
import sys
import halyk_agent.solver.solve  # noqa: F401
forbidden = {
    "halyk_agent.preflight.discover",
    "halyk_agent.preflight.quarantine",
    "halyk_agent.preflight.service",
    "halyk_agent.training",
    "halyk_agent.training.scorer",
}
loaded = forbidden.intersection(sys.modules)
assert not loaded, f"forbidden modules loaded: {sorted(loaded)}"
# DTO module is allowed
assert "halyk_agent.preflight.models" in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={**dict(**__import__("os").environ), "PYTHONPATH": str(ROOT / "src")},
    )
    assert result.returncode == 0, result.stdout + result.stderr
