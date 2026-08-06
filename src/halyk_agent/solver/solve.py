"""Competition solve orchestration (baseline only in Stage 5A)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from halyk_agent.solver.audit import RunFileAudit
from halyk_agent.solver.dataset.adapter import discover_dataset
from halyk_agent.solver.dataset.answer_key_guard import block_answer_key_read
from halyk_agent.solver.errors import DatasetAdapterError, LeakageAttemptError
from halyk_agent.solver.failures import FailureEvent
from halyk_agent.solver.mode import SolverMode, get_solver_mode
from halyk_agent.solver.submission.baseline import write_baseline_submission


def solve_dataset(
    dataset_root: Path,
    output_dir: Path,
    *,
    team: str | None = None,
    contact_email: str | None = None,
    model_name: str | None = None,
    run_id: str | None = None,
) -> dict[str, str]:
    """Competition path: dataset -> schema-valid unresolved baseline submission."""
    mode = get_solver_mode()
    if mode is SolverMode.TRAINING:
        # Still allowed to produce baseline, but never score here.
        pass

    rid = run_id or uuid.uuid4().hex
    audit = RunFileAudit(run_id=rid)
    failures: list[FailureEvent] = []
    manifest = discover_dataset(dataset_root, audit=audit, failure_events=failures)
    if manifest.submission_template is None:
        raise DatasetAdapterError("submission template missing")

    template_path = Path(manifest.submission_template.path)
    # Block if someone swapped template with answer key.
    block_answer_key_read(template_path)
    raw = template_path.read_bytes()
    audit.record(template_path, component="dataset", purpose="submission_template", data=raw)
    # Also record ledger + case files as opened inputs (not GT).
    if manifest.primary_ledger is not None:
        ledger_path = Path(manifest.primary_ledger.path)
        audit.record(
            ledger_path,
            component="dataset",
            purpose="primary_ledger",
            data=ledger_path.read_bytes(),
        )
    for case in manifest.case_descriptions:
        case_path = Path(case.path)
        audit.record(
            case_path, component="dataset", purpose="case_description", data=case_path.read_bytes()
        )

    payload = json.loads(raw.decode("utf-8"))
    write_baseline_submission(
        payload,
        output_dir,
        run_id=rid,
        audit=audit,
        team=team,
        contact_email=contact_email,
        model_name=model_name,
        failure_events=failures,
    )
    try:
        audit.assert_no_ground_truth()
    except LeakageAttemptError:
        raise
    # Explicit: ground_truth_candidate must not be in opened files.
    if manifest.ground_truth_candidate is not None:
        gt_path = Path(manifest.ground_truth_candidate.path).resolve()
        for item in audit.files:
            if Path(item.path).resolve() == gt_path:
                raise LeakageAttemptError("ground truth was opened during competition solve")

    # Omit GT candidate path so competition outputs stay identical regardless of GT presence.
    manifest_payload = manifest.model_dump(mode="json")
    if get_solver_mode() is SolverMode.COMPETITION:
        manifest_payload["ground_truth_candidate"] = None
        manifest_payload["ground_truth_detected"] = manifest.ground_truth_candidate is not None
    (output_dir / "dataset_manifest.json").write_text(
        json.dumps(manifest_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return {
        "submission": str((output_dir / "submission.json").as_posix()),
        "run_manifest": str((output_dir / "run_manifest.json").as_posix()),
        "summary": str((output_dir / "solver_summary.md").as_posix()),
        "run_id": rid,
    }
