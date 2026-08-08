"""Stage 9 independent-run comparison contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from halyk_agent.app.reproduction import ReproductionError, verify_reproduction_pair


def _run(root: Path, *, run_id: str, submission_actual: float = 1.0) -> None:
    root.mkdir()
    (root / "submission.json").write_text(
        json.dumps({"answers": {"P1": {"6.1": {"actual": submission_actual}}}}) + "\n",
        encoding="utf-8",
    )
    (root / "fallback_cells.jsonl").write_text("", encoding="utf-8")
    (root / "pipeline_manifest.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "ground_truth_access": "none",
                "source_read_count": 204,
                "evaluation_manifest_sha256": "e" * 64,
                "submission_sha256": "s" * 64,
                "stage_summary": {"evaluation": {"resolved": 29}},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def test_reproduction_ignores_run_id_but_compares_stable_outputs(tmp_path: Path) -> None:
    left = tmp_path / "a"
    right = tmp_path / "b"
    _run(left, run_id="a")
    _run(right, run_id="b")
    report = verify_reproduction_pair(left, right, tmp_path / "report")
    assert report.deterministic is True
    assert report.run_a_source_reads == report.run_b_source_reads == 204


def test_reproduction_fails_on_submission_drift(tmp_path: Path) -> None:
    left = tmp_path / "a"
    right = tmp_path / "b"
    _run(left, run_id="a", submission_actual=1.0)
    _run(right, run_id="b", submission_actual=1.1)
    with pytest.raises(ReproductionError):
        verify_reproduction_pair(left, right, tmp_path / "report")
