"""Parsing test configuration."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _disable_torch_compile_for_docling(request: pytest.FixtureRequest) -> None:
    """Avoid Windows torch inductor requiring MSVC `cl` during Docling smoke."""
    if request.node.get_closest_marker("docling") is None:
        return
    os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")
    os.environ.setdefault("TORCH_COMPILE_DISABLE", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
