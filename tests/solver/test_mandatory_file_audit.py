"""B1-b: mandatory audited file opens."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from halyk_agent.preflight.service import run_preflight
from halyk_agent.solver.errors import LeakageAttemptError
from halyk_agent.solver.filesystem import RecordingFileOpener
from halyk_agent.solver.solve import solve_from_manifest


def _mini(root: Path) -> None:
    (root / "CASE.ru.md").write_text("# Case\nCovenant limit scenario\n", encoding="utf-8")
    (root / "CASE.kz.md").write_text("# Case\nCovenant лимит scenario\n", encoding="utf-8")
    (root / "master_ledger_2025.csv").write_text(
        "txn_id,date,account_id,counterparty,description,amount,currency\n"
        "T1,2025-01-01,A,B,d,1,KZT\n",
        encoding="utf-8",
    )
    template = {
        "team": "t",
        "contact_email": "a@b.c",
        "model": "baseline",
        "answers": {"P1": {"6.1": {"status": None, "actual": None, "evidence_txn_id": None}}},
    }
    (root / "submission_template.json").write_text(json.dumps(template) + "\n", encoding="utf-8")
    (root / "documents").mkdir()
    (root / "documents" / "a.pdf").write_bytes(b"%PDF-1.4")
    (root / "ground_truth.json").write_text(
        json.dumps(
            {
                "scenarios": {
                    "P1": {
                        "covenants": {
                            "6.1": {"status": "BREACH", "actual": 1.0, "evidence_txn_id": "T1"}
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )


class _UnauditedOpener:
    def read_bytes(self, path: Path) -> bytes:
        return path.read_bytes()


class _NoneOpenedPathsOpener:
    """Satisfies Protocol attribute presence but returns None (fail closed)."""

    @property
    def opened_paths(self):  # type: ignore[no-untyped-def]
        return None

    def read_bytes(self, path: Path) -> bytes:
        return path.read_bytes()


def test_opened_paths_none_raises_leakage_not_typeerror(tmp_path: Path) -> None:
    _mini(tmp_path)
    manifest = run_preflight(tmp_path, tmp_path / "pf")
    out = tmp_path / "out-none"
    with pytest.raises(LeakageAttemptError, match="opened_paths"):
        solve_from_manifest(manifest, out, opener=_NoneOpenedPathsOpener())  # type: ignore[arg-type]
    assert not (out / "submission.json").exists()


def test_unaudited_opener_rejected_before_outputs(tmp_path: Path) -> None:
    _mini(tmp_path)
    manifest = run_preflight(tmp_path, tmp_path / "pf")
    out = tmp_path / "out"
    with pytest.raises(LeakageAttemptError, match="opened_paths"):
        solve_from_manifest(manifest, out, opener=_UnauditedOpener())  # type: ignore[arg-type]
    assert not (out / "submission.json").exists()


def test_malicious_template_role_pointing_at_quarantine_rejected(tmp_path: Path) -> None:
    _mini(tmp_path)
    manifest = run_preflight(tmp_path, tmp_path / "pf")
    # Point template path at quarantined ground truth.
    poisoned = manifest.model_copy(
        update={
            "submission_template": manifest.submission_template.model_copy(
                update={"path": manifest.quarantined[0].path}
            )
        }
    )
    out = tmp_path / "out-poison"
    with pytest.raises(LeakageAttemptError, match="quarantine"):
        solve_from_manifest(poisoned, out, opener=RecordingFileOpener())
    assert not (out / "submission.json").exists()


def test_allowed_opens_are_recorded(tmp_path: Path) -> None:
    _mini(tmp_path)
    manifest = run_preflight(tmp_path, tmp_path / "pf")
    opener = RecordingFileOpener()
    out = tmp_path / "out-ok"
    solve_from_manifest(manifest, out, opener=opener, run_id="audit")
    names = {p.name for p in opener.opened_paths}
    assert "submission_template.json" in names
    assert "ground_truth.json" not in names
    assert (out / "submission.json").is_file()
