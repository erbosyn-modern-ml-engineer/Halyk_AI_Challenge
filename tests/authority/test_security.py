"""Security boundary: Stage 5C does not open ground truth."""

from __future__ import annotations

from pathlib import Path

import pytest

from halyk_agent.app import authority as authority_mod
from halyk_agent.app.authority import AuthorityServiceError, authority_from_paths


def test_authority_missing_routing_dir(tmp_path: Path) -> None:
    with pytest.raises(AuthorityServiceError):
        authority_from_paths(
            routing_dir=tmp_path / "missing",
            parsed_dir=tmp_path / "parsed",
            output_dir=tmp_path / "out",
        )


def test_authority_does_not_import_ground_truth_loader() -> None:
    source = Path(authority_mod.__file__).read_text(encoding="utf-8")
    assert "ground_truth" not in source
    assert "discover_and_sanitize" not in source
    assert "load_sanitized_manifest" not in source
