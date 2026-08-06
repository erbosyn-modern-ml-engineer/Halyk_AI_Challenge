"""Isolated public training scorer (Decimal only)."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

from halyk_agent.solver.mode import require_training_mode
from halyk_agent.solver.submission.models import CovenantStatus, SubmissionDocument
from halyk_agent.training.models import CellScore, ScoreReport

D = Decimal
HALF = D("0.50")
ACTUAL_WEIGHT = D("0.30")
EVIDENCE_WEIGHT = D("0.20")
REL_TOL = D("0.05")
ZERO = D("0")
ONE = D("1")


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    return D(str(value))


def _relative_error(submitted: Decimal | None, expected: Decimal) -> Decimal:
    if submitted is None:
        return D("Infinity")
    if expected.copy_abs() == ZERO:
        return ZERO if submitted == expected else D("Infinity")
    return (submitted - expected).copy_abs() / expected.copy_abs()


def _actual_component(rel_err: Decimal) -> Decimal:
    if rel_err.is_infinite():
        return ZERO
    factor = ONE - (rel_err / REL_TOL)
    if factor < ZERO:
        factor = ZERO
    return ACTUAL_WEIGHT * factor


def score_cell(
    *,
    submitted: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> CellScore:
    scenario_id = str(expected.get("_scenario_id", ""))
    covenant_id = str(expected.get("_covenant_id", ""))
    exp_status = expected.get("status")
    exp_actual = _dec(expected.get("actual"))
    exp_evidence = expected.get("evidence_txn_id")
    sub_status = submitted.get("status")
    sub_actual = _dec(submitted.get("actual"))
    sub_evidence = submitted.get("evidence_txn_id")

    notes = ""
    status_ok = False
    if sub_status is None or exp_status is None:
        notes = "missing_status"
    else:
        try:
            status_ok = CovenantStatus(str(sub_status)) is CovenantStatus(str(exp_status))
        except ValueError:
            notes = "malformed_status"
            status_ok = False
        if not status_ok and not notes:
            notes = "incorrect_status"

    if not status_ok or exp_actual is None:
        return CellScore(
            scenario_id=scenario_id,
            covenant_id=covenant_id,
            cell_score=ZERO,
            status_ok=False,
            relative_error=None,
            actual_component=ZERO,
            evidence_component=ZERO,
            notes=notes or "gated_to_zero",
        )

    rel = _relative_error(sub_actual, exp_actual)
    actual_component = _actual_component(rel)
    if exp_evidence is not None:
        evidence_component = (
            EVIDENCE_WEIGHT
            if sub_evidence is not None and str(sub_evidence) == str(exp_evidence)
            else ZERO
        )
    else:
        # null evidence: decay with relative error
        if rel.is_infinite():
            evidence_component = ZERO
        else:
            factor = ONE - (rel / REL_TOL)
            if factor < ZERO:
                factor = ZERO
            evidence_component = EVIDENCE_WEIGHT * factor

    cell_score = HALF + actual_component + evidence_component
    return CellScore(
        scenario_id=scenario_id,
        covenant_id=covenant_id,
        cell_score=cell_score,
        status_ok=True,
        relative_error=None if rel.is_infinite() else rel,
        actual_component=actual_component,
        evidence_component=evidence_component,
        notes=notes,
    )


def _load_ground_truth(path: Path) -> dict[str, dict[str, dict[str, Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, dict):
        raise ValueError("ground truth missing scenarios")
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for scenario_id, scenario in scenarios.items():
        covenants = scenario.get("covenants") if isinstance(scenario, dict) else None
        if not isinstance(covenants, dict):
            continue
        out[str(scenario_id)] = {
            str(k): dict(v) for k, v in covenants.items() if isinstance(v, dict)
        }
    return out


def score_submission(
    submission_path: Path,
    ground_truth_path: Path,
    output_dir: Path,
    *,
    difficulty_weights: Mapping[str, Decimal] | None = None,
) -> ScoreReport:
    """Score a submission against ground truth. Requires HALYK_MODE=training."""
    require_training_mode()
    submission = SubmissionDocument.model_validate_json(submission_path.read_text(encoding="utf-8"))
    expected = _load_ground_truth(ground_truth_path)

    cells: list[CellScore] = []
    missing: list[str] = []
    extra: list[str] = []
    malformed: list[str] = []

    expected_keys = {(s, c) for s, cov in expected.items() for c in cov}
    submitted_keys = {(s, c) for s, cov in submission.answers.items() for c in cov}
    for key in sorted(expected_keys - submitted_keys):
        missing.append(f"{key[0]}/{key[1]}")
    for key in sorted(submitted_keys - expected_keys):
        extra.append(f"{key[0]}/{key[1]}")

    weighted_total = ZERO
    weights_used = False
    for scenario_id, covenants in sorted(expected.items()):
        for covenant_id, exp_cell in sorted(covenants.items()):
            submitted_cell = submission.answers.get(scenario_id, {}).get(covenant_id)
            if submitted_cell is None:
                cells.append(
                    CellScore(
                        scenario_id=scenario_id,
                        covenant_id=covenant_id,
                        cell_score=ZERO,
                        status_ok=False,
                        relative_error=None,
                        actual_component=ZERO,
                        evidence_component=ZERO,
                        notes="missing_cell",
                    )
                )
                continue
            payload = submitted_cell.model_dump(mode="json")
            exp_payload = dict(exp_cell)
            exp_payload["_scenario_id"] = scenario_id
            exp_payload["_covenant_id"] = covenant_id
            scored = score_cell(submitted=payload, expected=exp_payload)
            if scored.notes == "malformed_status":
                malformed.append(f"{scenario_id}/{covenant_id}")
            cells.append(scored)
            if difficulty_weights is not None:
                weight = D(str(difficulty_weights.get(f"{scenario_id}/{covenant_id}", ONE)))
                weighted_total += scored.cell_score * weight
                weights_used = True

    uniform_total = sum((c.cell_score for c in cells), ZERO)
    mean = uniform_total / D(len(cells)) if cells else ZERO
    report = ScoreReport(
        uniform_total=uniform_total,
        uniform_mean_cell=mean,
        weighted_total=weighted_total if weights_used else None,
        official_weights_known=False,
        weights_label="unknown_official_weights",
        cell_count=len(cells),
        cells=cells,
        missing_cells=missing,
        extra_cells=extra,
        malformed_cells=malformed,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "score_report.json"
    text = (
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    )
    tmp = report_path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, report_path)
    summary = "\n".join(
        [
            "# Score summary",
            "",
            f"- uniform_total: `{report.uniform_total}`",
            f"- uniform_mean_cell: `{report.uniform_mean_cell}`",
            f"- cell_count: {report.cell_count}",
            f"- official_weights: `{report.weights_label}`",
            f"- missing_cells: {len(report.missing_cells)}",
            f"- extra_cells: {len(report.extra_cells)}",
            "",
            "Synthetic/public formula only — not an official leaderboard claim.",
            "",
        ]
    )
    (output_dir / "score_summary.md").write_text(summary, encoding="utf-8", newline="\n")
    return report
