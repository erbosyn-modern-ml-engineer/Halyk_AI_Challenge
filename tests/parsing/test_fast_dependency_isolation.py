"""Dependency isolation assertions for FAST profile."""

from __future__ import annotations

from pathlib import Path


def test_docling_is_optional_extra_only() -> None:
    root = Path(__file__).resolve().parents[2]
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    before_optional, _, after = text.partition("[project.optional-dependencies]")
    assert "docling" not in before_optional.lower()
    assert "docling" in after.lower()


def test_importing_package_and_fast_parser_without_loading_docling() -> None:
    import sys

    before = {name for name in sys.modules if name == "docling" or name.startswith("docling.")}
    import halyk_agent  # noqa: F401
    from halyk_agent.adapters.parsing.pypdf_parser import PyPdfDocumentParser

    _ = PyPdfDocumentParser()
    after = {name for name in sys.modules if name == "docling" or name.startswith("docling.")}
    assert after == before
