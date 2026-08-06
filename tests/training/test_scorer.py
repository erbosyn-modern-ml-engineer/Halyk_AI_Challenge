"""Training scorer Decimal formula tests."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from halyk_agent.solver.mode import ModeError
from halyk_agent.training.scorer import score_cell, score_submission


def test_status_gates_to_zero() -> None:
    scored = score_cell(
        submitted={"status": "COMPLIANT", "actual": 100, "evidence_txn_id": None},
        expected={
            "status": "BREACH",
            "actual": 100,
            "evidence_txn_id": None,
            "_scenario_id": "P1",
            "_covenant_id": "6.1",
        },
    )
    assert scored.cell_score == Decimal("0")


def test_actual_error_boundaries() -> None:
    # e=0 => actual_component=0.30
    perfect = score_cell(
        submitted={"status": "BREACH", "actual": 100, "evidence_txn_id": None},
        expected={
            "status": "BREACH",
            "actual": 100,
            "evidence_txn_id": None,
            "_scenario_id": "P1",
            "_covenant_id": "6.1",
        },
    )
    assert perfect.actual_component == Decimal("0.30")
    assert perfect.cell_score == Decimal("0.50") + Decimal("0.30") + Decimal("0.20")

    # e=0.025 => 0.30 * 0.5 = 0.15
    mid = score_cell(
        submitted={"status": "BREACH", "actual": 102.5, "evidence_txn_id": None},
        expected={
            "status": "BREACH",
            "actual": 100,
            "evidence_txn_id": None,
            "_scenario_id": "P1",
            "_covenant_id": "6.1",
        },
    )
    assert mid.actual_component == Decimal("0.15")

    # e=0.05 => 0
    edge = score_cell(
        submitted={"status": "BREACH", "actual": 105, "evidence_txn_id": None},
        expected={
            "status": "BREACH",
            "actual": 100,
            "evidence_txn_id": None,
            "_scenario_id": "P1",
            "_covenant_id": "6.1",
        },
    )
    assert edge.actual_component == Decimal("0")


def test_evidence_exact_and_null_decay() -> None:
    exact = score_cell(
        submitted={"status": "BREACH", "actual": 100, "evidence_txn_id": "T1"},
        expected={
            "status": "BREACH",
            "actual": 100,
            "evidence_txn_id": "T1",
            "_scenario_id": "P1",
            "_covenant_id": "6.1",
        },
    )
    assert exact.evidence_component == Decimal("0.20")
    miss = score_cell(
        submitted={"status": "BREACH", "actual": 100, "evidence_txn_id": "TX"},
        expected={
            "status": "BREACH",
            "actual": 100,
            "evidence_txn_id": "T1",
            "_scenario_id": "P1",
            "_covenant_id": "6.1",
        },
    )
    assert miss.evidence_component == Decimal("0")


def test_actual_zero_policy() -> None:
    ok = score_cell(
        submitted={"status": "COMPLIANT", "actual": 0, "evidence_txn_id": None},
        expected={
            "status": "COMPLIANT",
            "actual": 0,
            "evidence_txn_id": None,
            "_scenario_id": "P1",
            "_covenant_id": "6.1",
        },
    )
    assert ok.actual_component == Decimal("0.30")
    bad = score_cell(
        submitted={"status": "COMPLIANT", "actual": 1, "evidence_txn_id": None},
        expected={
            "status": "COMPLIANT",
            "actual": 0,
            "evidence_txn_id": None,
            "_scenario_id": "P1",
            "_covenant_id": "6.1",
        },
    )
    assert bad.actual_component == Decimal("0")


def test_score_submission_requires_training_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HALYK_MODE", "competition")
    sub = tmp_path / "submission.json"
    gt = tmp_path / "ground_truth.json"
    sub.write_text(
        json.dumps(
            {
                "team": "t",
                "contact_email": "a@b.c",
                "model": "m",
                "answers": {
                    "P1": {"6.1": {"status": None, "actual": None, "evidence_txn_id": None}}
                },
            }
        ),
        encoding="utf-8",
    )
    gt.write_text(
        json.dumps(
            {
                "scenarios": {
                    "P1": {
                        "covenants": {
                            "6.1": {"status": "BREACH", "actual": 1, "evidence_txn_id": None}
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ModeError):
        score_submission(sub, gt, tmp_path / "out")
