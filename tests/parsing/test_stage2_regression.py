"""Stage 2 / Stage 3 regression guards for package identity and banned deps."""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_canonical_package_imports() -> None:
    import halyk_agent

    assert halyk_agent.__name__ == "halyk_agent"


def test_misspelled_package_absent() -> None:
    assert importlib.util.find_spec("haliyk_agent") is None


def test_module_help_succeeds() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "halyk_agent", "--help"],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0
    assert "inspect" in result.stdout
    assert "parse" in result.stdout


def test_no_pymupdf_or_fitz_dependency_or_import() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8").lower()
    assert "pymupdf" not in pyproject
    assert re.search(r"(?m)^\s*fitz\s*=", pyproject) is None
    forbidden = re.compile(
        r"(?m)^\s*(import\s+fitz\b|from\s+fitz\b|import\s+pymupdf\b|from\s+pymupdf\b)"
    )
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert forbidden.search(text) is None, path


def test_stage2_inspect_cli_still_works(tmp_path: Path) -> None:
    from tests.ingestion.helpers import sample_transactions_csv, write_zip

    archive = tmp_path / "a.zip"
    write_zip(archive, {"transactions.csv": sample_transactions_csv()})
    out = tmp_path / "out"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "halyk_agent",
            "inspect",
            "--input",
            str(archive),
            "--output",
            str(out),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0
    assert (out / "manifest.json").is_file()
