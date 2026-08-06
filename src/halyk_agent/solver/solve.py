"""Competition solve orchestration — sanitized manifest only (no raw dataset root)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from halyk_agent.preflight.models import SanitizedDatasetManifest
from halyk_agent.solver.audit import RunFileAudit
from halyk_agent.solver.errors import DatasetAdapterError, LeakageAttemptError
from halyk_agent.solver.failures import FailureEvent
from halyk_agent.solver.filesystem import FileOpener, RecordingFileOpener
from halyk_agent.solver.submission.baseline import write_baseline_submission

_ALLOWED_PURPOSES = frozenset(
    {
        "submission_template",
        "primary_ledger",
        "case_description",
        "baseline_write",
    }
)


def solve_from_manifest(
    manifest: SanitizedDatasetManifest,
    output_dir: Path,
    *,
    team: str | None = None,
    contact_email: str | None = None,
    model_name: str | None = None,
    run_id: str | None = None,
    opener: FileOpener | None = None,
) -> dict[str, str]:
    """Competition path: sanitized manifest → schema-valid unresolved baseline.

    The solver never receives a dataset root, never discovers JSON candidates,
    and never opens quarantined answer-key paths.
    """
    rid = run_id or uuid.uuid4().hex
    audit = RunFileAudit(run_id=rid)
    failures: list[FailureEvent] = []
    file_opener: FileOpener = opener or RecordingFileOpener()

    template_path = Path(manifest.submission_template.path)
    raw = file_opener.read_bytes(template_path)
    audit.record(template_path, component="dataset", purpose="submission_template", data=raw)

    ledger_path = Path(manifest.primary_ledger.path)
    audit.record(
        ledger_path,
        component="dataset",
        purpose="primary_ledger",
        data=file_opener.read_bytes(ledger_path),
    )
    for case in manifest.case_descriptions:
        case_path = Path(case.path)
        audit.record(
            case_path,
            component="dataset",
            purpose="case_description",
            data=file_opener.read_bytes(case_path),
        )

    # Reject any accidental quarantine path in allowlisted slots.
    banned = {Path(item.path).resolve() for item in manifest.quarantined}
    for opened in getattr(file_opener, "opened_paths", []):
        if Path(opened).resolve() in banned:
            raise LeakageAttemptError(f"solver opened quarantined path: {opened}")

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DatasetAdapterError(f"invalid submission template JSON: {exc}") from exc

    # Template must not carry scored answer values (defense in depth).
    if _template_looks_like_scored_answers(payload):
        raise LeakageAttemptError("allowlisted template appears to contain scored answers")

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
    audit.assert_no_ground_truth()
    for item in audit.files:
        if item.purpose not in _ALLOWED_PURPOSES:
            raise LeakageAttemptError(f"unexpected solver open purpose: {item.purpose}")
        if "ground_truth" in Path(item.path).name.lower():
            raise LeakageAttemptError(f"ground truth path in run_manifest: {item.path}")
        if Path(item.path).resolve() in banned:
            raise LeakageAttemptError(f"quarantined path in run_manifest: {item.path}")

    # Solver copies only allowlisted refs into its side manifest (no quarantine list values).
    solver_side = {
        "schema_version": manifest.schema_version,
        "submission_template": manifest.submission_template.model_dump(mode="json"),
        "primary_ledger": manifest.primary_ledger.model_dump(mode="json"),
        "case_descriptions": [c.model_dump(mode="json") for c in manifest.case_descriptions],
        "document_count": len(manifest.document_files),
        "quarantined_count": len(manifest.quarantined),
    }
    (output_dir / "sanitized_manifest_used.json").write_text(
        json.dumps(solver_side, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return {
        "submission": str((output_dir / "submission.json").as_posix()),
        "run_manifest": str((output_dir / "run_manifest.json").as_posix()),
        "summary": str((output_dir / "solver_summary.md").as_posix()),
        "run_id": rid,
    }


def _template_looks_like_scored_answers(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        return False
    for scenario in answers.values():
        if not isinstance(scenario, dict):
            continue
        for cell in scenario.values():
            if isinstance(cell, dict) and (
                cell.get("status") is not None or cell.get("actual") is not None
            ):
                return True
    return False
