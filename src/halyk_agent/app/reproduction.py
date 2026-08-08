"""Stage 9 deterministic comparison of two independently completed solver runs."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from halyk_agent.adapters.archive.hashing import sha256_file


class ReproductionError(Exception):
    def __init__(self, message: str, *, code: str = "REPRODUCTION_MISMATCH") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class ReproductionReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "halyk.reproduction.v1"
    deterministic: bool
    submission_sha256: str
    fallback_diagnostics_sha256: str
    evaluation_manifest_sha256: str
    run_a_source_reads: int
    run_b_source_reads: int
    run_a_ground_truth_access: str
    run_b_ground_truth_access: str


def _stable_pipeline_payload(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ReproductionError("pipeline manifest is not a JSON object")
    return {key: value for key, value in payload.items() if key != "run_id"}


def verify_reproduction_pair(
    run_a: Path,
    run_b: Path,
    output_dir: Path,
) -> ReproductionReport:
    """Compare two solves launched in independent processes/environments.

    Stage 9 intentionally separates *launching* from *comparison*: the two solves
    should be started as independent CLI processes (or on two fresh machines/CI
    jobs), avoiding hidden in-process caches and multiprocessing state.
    """

    run_a = run_a.resolve()
    run_b = run_b.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    submission_a = run_a / "submission.json"
    submission_b = run_b / "submission.json"
    fallback_a = run_a / "fallback_cells.jsonl"
    fallback_b = run_b / "fallback_cells.jsonl"
    pipeline_a = run_a / "pipeline_manifest.json"
    pipeline_b = run_b / "pipeline_manifest.json"
    required = (
        submission_a,
        submission_b,
        fallback_a,
        fallback_b,
        pipeline_a,
        pipeline_b,
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ReproductionError(f"reproduction outputs missing: {missing}")

    if submission_a.read_bytes() != submission_b.read_bytes():
        raise ReproductionError("submission.json differs across independent runs")
    if fallback_a.read_bytes() != fallback_b.read_bytes():
        raise ReproductionError("fallback diagnostics differ across independent runs")
    stable_a = _stable_pipeline_payload(pipeline_a)
    stable_b = _stable_pipeline_payload(pipeline_b)
    if stable_a != stable_b:
        raise ReproductionError("stable pipeline manifest fields differ across independent runs")

    gt_a = str(stable_a.get("ground_truth_access"))
    gt_b = str(stable_b.get("ground_truth_access"))
    if gt_a != "none" or gt_b != "none":
        raise ReproductionError("ground-truth access was not zero in a reproduction run")

    report = ReproductionReport(
        deterministic=True,
        submission_sha256=sha256_file(submission_a),
        fallback_diagnostics_sha256=sha256_file(fallback_a),
        evaluation_manifest_sha256=str(stable_a["evaluation_manifest_sha256"]),
        run_a_source_reads=int(stable_a["source_read_count"]),
        run_b_source_reads=int(stable_b["source_read_count"]),
        run_a_ground_truth_access=gt_a,
        run_b_ground_truth_access=gt_b,
    )
    (output_dir / "reproduction_report.json").write_text(
        report.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (output_dir / "reproduction_summary.md").write_text(
        "\n".join(
            [
                "# Independent-run reproduction",
                "",
                "- deterministic: `true`",
                f"- submission_sha256: `{report.submission_sha256}`",
                f"- evaluation_manifest_sha256: `{report.evaluation_manifest_sha256}`",
                f"- source_reads: `{report.run_a_source_reads}` / `{report.run_b_source_reads}`",
                "- ground_truth_access: `none` / `none`",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )
    return report
