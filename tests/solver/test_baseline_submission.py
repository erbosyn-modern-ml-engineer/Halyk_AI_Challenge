"""Baseline submission schema preservation."""

from __future__ import annotations

import json
from pathlib import Path

from halyk_agent.solver.audit import RunFileAudit
from halyk_agent.solver.submission.baseline import write_baseline_submission


def test_baseline_preserves_all_template_cells(tmp_path: Path) -> None:
    template = {
        "team": "demo",
        "contact_email": "demo@example.com",
        "model": "baseline",
        "answers": {
            "P2": {
                "6.2": {"status": None, "actual": None, "evidence_txn_id": None},
                "6.1": {"status": None, "actual": None, "evidence_txn_id": None},
            },
            "P1": {"6.1": {"status": None, "actual": None, "evidence_txn_id": None}},
        },
    }
    out = tmp_path / "out"
    audit = RunFileAudit(run_id="r1")
    doc = write_baseline_submission(template, out, run_id="r1", audit=audit)
    assert set(doc.answers) == {"P1", "P2"}
    assert set(doc.answers["P2"]) == {"6.1", "6.2"}
    raw = (out / "submission.json").read_text(encoding="utf-8")
    raw2 = (out / "submission.json").read_text(encoding="utf-8")
    assert raw == raw2
    payload = json.loads(raw)
    assert payload["answers"]["P1"]["6.1"]["status"] is None
    unresolved = (out / "unresolved_cells.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(unresolved) == 3
