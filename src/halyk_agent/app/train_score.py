"""Application wrapper for training scorer (lazy-imported by CLI only)."""

from __future__ import annotations

from pathlib import Path

from halyk_agent.training.models import ScoreReport


def run_train_score(dataset: Path, submission: Path, output: Path) -> ScoreReport:
    from halyk_agent.training.scorer import score_submission

    gt = dataset / "ground_truth.json"
    if not gt.is_file():
        matches = sorted(dataset.rglob("ground_truth.json"))
        if not matches:
            raise FileNotFoundError("ground_truth.json not found under dataset")
        gt = matches[0]
    return score_submission(submission, gt, output)
