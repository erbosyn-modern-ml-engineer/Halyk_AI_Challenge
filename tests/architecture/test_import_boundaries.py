"""Architecture boundary tests for package dependency direction."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOMAIN_ROOT = ROOT / "src" / "halyk_agent" / "domain"

FORBIDDEN_DOMAIN_PREFIXES = (
    "halyk_agent.contracts",
    "halyk_agent.adapters",
    "halyk_agent.profiles",
    "halyk_agent.app",
    "fastapi",
    "sqlalchemy",
    "redis",
    "langgraph",
    "docling",
)


def _iter_python_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


@pytest.mark.parametrize(
    "path", _iter_python_files(DOMAIN_ROOT), ids=lambda p: str(p.relative_to(ROOT))
)
def test_domain_does_not_import_forbidden_packages(path: Path) -> None:
    imported = _imported_modules(path)
    violations = sorted(
        module
        for module in imported
        if any(
            module == prefix or module.startswith(f"{prefix}.")
            for prefix in FORBIDDEN_DOMAIN_PREFIXES
        )
    )
    assert violations == [], f"{path} imports forbidden modules: {violations}"


def test_contracts_may_import_domain() -> None:
    contracts_init = ROOT / "src" / "halyk_agent" / "contracts" / "connectors.py"
    imported = _imported_modules(contracts_init)
    assert any(module.startswith("halyk_agent.domain") for module in imported)
