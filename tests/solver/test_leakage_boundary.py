"""B1: real ground-truth process/memory isolation with recorded opens."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from halyk_agent.preflight.models import SanitizedDatasetManifest
from halyk_agent.preflight.quarantine import is_answer_key_payload
from halyk_agent.preflight.service import run_preflight
from halyk_agent.solver.filesystem import RecordingFileOpener
from halyk_agent.solver.solve import solve_from_manifest


def _mini(root: Path, *, with_gt: bool = True, gt_payload: dict | None = None) -> None:
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
        "answers": {
            "P1": {"6.1": {"status": None, "actual": None, "evidence_txn_id": None}},
            "P2": {"6.1": {"status": None, "actual": None, "evidence_txn_id": None}},
        },
    }
    (root / "submission_template.json").write_text(json.dumps(template) + "\n", encoding="utf-8")
    (root / "documents").mkdir()
    (root / "documents" / "a.pdf").write_bytes(b"%PDF-1.4")
    if with_gt:
        payload = gt_payload or {
            "version": 1,
            "seed": 1,
            "scenarios": {
                "P1": {
                    "covenants": {
                        "6.1": {"status": "BREACH", "actual": 1.0, "evidence_txn_id": "T1"}
                    }
                },
                "P2": {
                    "covenants": {
                        "6.1": {"status": "COMPLIANT", "actual": 0.0, "evidence_txn_id": None}
                    }
                },
            },
        }
        (root / "ground_truth.json").write_text(json.dumps(payload) + "\n", encoding="utf-8")


def test_answer_key_shape_detected() -> None:
    assert is_answer_key_payload(
        {
            "scenarios": {
                "P1": {
                    "covenants": {"6.1": {"status": "BREACH", "actual": 1, "evidence_txn_id": "T"}}
                }
            }
        }
    )


def test_solver_api_has_no_raw_dataset_root_parameter() -> None:
    params = inspect.signature(solve_from_manifest).parameters
    assert "dataset_root" not in params
    assert "dataset" not in params
    assert "manifest" in params


def test_preflight_quarantines_named_and_renamed_answer_keys(tmp_path: Path) -> None:
    _mini(tmp_path, with_gt=True)
    renamed = {
        "scenarios": {
            "P1": {"covenants": {"6.1": {"status": "BREACH", "actual": 9, "evidence_txn_id": "TX"}}}
        }
    }
    (tmp_path / "answers_secret.json").write_text(json.dumps(renamed), encoding="utf-8")
    manifest = run_preflight(tmp_path, tmp_path / "pf")
    reasons = {item.quarantine_reason for item in manifest.quarantined}
    paths = {Path(item.path).name for item in manifest.quarantined}
    assert "filename_ground_truth" in reasons
    assert "content_shape_answer_key" in reasons
    assert "ground_truth.json" in paths
    assert "answers_secret.json" in paths
    dump = json.dumps(manifest.model_dump(mode="json"))
    assert "BREACH" not in dump
    assert '"actual": 9' not in dump
    assert '"actual": 1.0' not in dump


def test_solver_opens_only_allowlisted_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HALYK_MODE", "competition")
    _mini(tmp_path, with_gt=True)
    (tmp_path / "answers_secret.json").write_text(
        json.dumps(
            {
                "scenarios": {
                    "P1": {
                        "covenants": {
                            "6.1": {"status": "BREACH", "actual": 9, "evidence_txn_id": "TX"}
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    manifest = run_preflight(tmp_path, tmp_path / "pf")
    opener = RecordingFileOpener()
    out = tmp_path / "out"
    solve_from_manifest(manifest, out, run_id="fixed-run", opener=opener)

    opened_names = {p.name for p in opener.opened_paths}
    assert "ground_truth.json" not in opened_names
    assert "answers_secret.json" not in opened_names
    assert "submission_template.json" in opened_names
    assert "master_ledger_2025.csv" in opened_names

    run_manifest = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    run_names = {Path(f["path"]).name for f in run_manifest["opened_files"]}
    assert "ground_truth.json" not in run_names
    assert "answers_secret.json" not in run_names
    assert all(
        Path(f["path"]).name
        in {
            "submission_template.json",
            "master_ledger_2025.csv",
            "CASE.ru.md",
            "CASE.kz.md",
            "submission.json",
        }
        for f in run_manifest["opened_files"]
    )


def test_solver_output_identical_across_gt_variants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HALYK_MODE", "competition")
    base = tmp_path / "base"
    base.mkdir()
    _mini(base, with_gt=True)
    (base / "answers_secret.json").write_text(
        json.dumps(
            {
                "scenarios": {
                    "P1": {
                        "covenants": {
                            "6.1": {"status": "BREACH", "actual": 9, "evidence_txn_id": "TX"}
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    def _solve(label: str) -> bytes:
        pf = tmp_path / f"pf-{label}"
        manifest = run_preflight(base, pf)
        # Solver must not receive quarantine answer values — only metadata counts.
        assert isinstance(manifest, SanitizedDatasetManifest)
        out = tmp_path / f"out-{label}"
        opener = RecordingFileOpener()
        solve_from_manifest(manifest, out, run_id="fixed-run", opener=opener)
        assert all("ground_truth" not in p.name.lower() for p in opener.opened_paths)
        assert all(p.name != "answers_secret.json" for p in opener.opened_paths)
        return (out / "submission.json").read_bytes()

    sub1 = _solve("present")
    (base / "ground_truth.json").unlink()
    (base / "answers_secret.json").unlink()
    sub2 = _solve("deleted")
    (base / "ground_truth.json").write_text("{not-json", encoding="utf-8")
    (base / "answers_secret.json").write_text(
        json.dumps(
            {
                "scenarios": {
                    "P1": {
                        "covenants": {
                            "6.1": {"status": "BREACH", "actual": 99, "evidence_txn_id": "ZZ"}
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    sub3 = _solve("corrupt-and-renamed")
    assert sub1 == sub2 == sub3
