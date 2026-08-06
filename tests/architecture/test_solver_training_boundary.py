"""Solver must not import training package."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOLVER_ROOT = ROOT / "src" / "halyk_agent" / "solver"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_solver_does_not_import_training() -> None:
    violations: list[str] = []
    for path in sorted(SOLVER_ROOT.rglob("*.py")):
        for module in _imports(path):
            if module == "halyk_agent.training" or module.startswith("halyk_agent.training."):
                violations.append(f"{path}:{module}")
    assert violations == []
