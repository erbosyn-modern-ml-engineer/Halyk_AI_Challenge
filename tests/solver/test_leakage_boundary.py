"""Ground-truth leakage and non-access tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from halyk_agent.solver.dataset.answer_key_guard import block_answer_key_read, is_answer_key_payload
from halyk_agent.solver.errors import AnswerKeyAccessBlockedError
from halyk_agent.solver.solve import solve_dataset


def _mini(root: Path, *, with_gt: bool = True, gt_payload: dict | None = None) -> None:
    (root / "CASE.ru.md").write_text("# Case\nCovenant limit scenario\n", encoding="utf-8")
    (root / "CASE.kz.md").write_text("# Case\nCovenant лимит scenario\n", encoding="utf-8")
    (root / "master_ledger_2025.csv").write_text(
        "txn_id,date,account_id,counterparty,description,amount,currency\nT1,2025-01-01,A,B,d,1,KZT\n",
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


def test_renamed_answer_key_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HALYK_MODE", "competition")
    path = tmp_path / "submission_template.json"
    path.write_text(
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
    with pytest.raises(AnswerKeyAccessBlockedError):
        block_answer_key_read(path)


def test_solver_output_identical_with_gt_variants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HALYK_MODE", "competition")
    base = tmp_path / "base"
    base.mkdir()
    _mini(base, with_gt=True)

    out1 = tmp_path / "out1"
    solve_dataset(base, out1, run_id="fixed-run")
    sub1 = (out1 / "submission.json").read_bytes()
    man1 = json.loads((out1 / "run_manifest.json").read_text(encoding="utf-8"))
    assert all("ground_truth" not in Path(f["path"]).name.lower() for f in man1["opened_files"])

    # delete GT
    (base / "ground_truth.json").unlink()
    out2 = tmp_path / "out2"
    solve_dataset(base, out2, run_id="fixed-run")
    assert (out2 / "submission.json").read_bytes() == sub1

    # corrupt / garbage GT
    (base / "ground_truth.json").write_text("{not-json", encoding="utf-8")
    out3 = tmp_path / "out3"
    solve_dataset(base, out3, run_id="fixed-run")
    assert (out3 / "submission.json").read_bytes() == sub1
