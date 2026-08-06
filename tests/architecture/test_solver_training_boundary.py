"""Solver must not import training, answer-key quarantine, or raw discovery."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOLVER_ROOT = ROOT / "src" / "halyk_agent" / "solver"
FORBIDDEN_PREFIXES = (
    "halyk_agent.training",
    "halyk_agent.preflight.quarantine",
    "halyk_agent.preflight.discover",
    "halyk_agent.preflight.service",
    "halyk_agent.preflight.ignore",
)


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


def test_solver_does_not_import_training_or_answer_key_parsers() -> None:
    violations: list[str] = []
    for path in sorted(SOLVER_ROOT.rglob("*.py")):
        for module in _imports(path):
            if any(
                module == prefix or module.startswith(prefix + ".") for prefix in FORBIDDEN_PREFIXES
            ):
                violations.append(f"{path}:{module}")
            if "answer_key" in module:
                violations.append(f"{path}:{module}")
    assert violations == []


def test_solver_may_import_sanitized_manifest_dto_only() -> None:
    allowed_preflight = {"halyk_agent.preflight.models", "halyk_agent.preflight"}
    for path in sorted(SOLVER_ROOT.rglob("*.py")):
        for module in _imports(path):
            if module.startswith("halyk_agent.preflight") and module not in allowed_preflight:
                raise AssertionError(f"solver imports non-DTO preflight module: {path}:{module}")
