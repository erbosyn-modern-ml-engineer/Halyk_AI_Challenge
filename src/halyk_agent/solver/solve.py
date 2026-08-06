"""Competition solve orchestration — sanitized manifest only (no raw dataset root)."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import uuid
from pathlib import Path

from halyk_agent.preflight.models import SanitizedDatasetManifest
from halyk_agent.solver.audit import RunFileAudit
from halyk_agent.solver.errors import DatasetAdapterError, LeakageAttemptError
from halyk_agent.solver.failures import FailureEvent
from halyk_agent.solver.filesystem import (
    FileOpener,
    RecordingFileOpener,
    require_audited_opener,
)
from halyk_agent.solver.submission.baseline import write_baseline_submission

_ALLOWED_PURPOSES = frozenset(
    {
        "submission_template",
        "primary_ledger",
        "case_description",
        "baseline_write",
    }
)


def _resolve(path: Path) -> Path:
    return path.expanduser().resolve()


def _validate_manifest_paths(manifest: SanitizedDatasetManifest) -> tuple[set[Path], set[Path]]:
    """Return (allowed, banned) resolved path sets. Fail if roles point at quarantine."""
    banned = {_resolve(Path(item.path)) for item in manifest.quarantined}
    allowed = {
        _resolve(Path(manifest.submission_template.path)),
        _resolve(Path(manifest.primary_ledger.path)),
        *{_resolve(Path(case.path)) for case in manifest.case_descriptions},
    }
    overlap = allowed & banned
    if overlap:
        paths = sorted(str(p) for p in overlap)
        raise LeakageAttemptError(f"sanitized manifest allowlist overlaps quarantine: {paths}")
    for path in allowed:
        if "ground_truth" in path.name.lower():
            raise LeakageAttemptError(f"allowlisted path looks like ground truth: {path}")
    return allowed, banned


def _assert_opens_allowlisted(
    opener: FileOpener,
    *,
    allowed: set[Path],
    banned: set[Path],
) -> None:
    opened = [_resolve(Path(p)) for p in opener.opened_paths]
    for path in opened:
        if path in banned:
            raise LeakageAttemptError(f"solver opened quarantined path: {path}")
        if path not in allowed:
            raise LeakageAttemptError(f"solver opened non-allowlisted path: {path}")


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
    failures: list[FailureEvent] = []

    # Fail closed before any reads / writes.
    file_opener = require_audited_opener(opener or RecordingFileOpener())
    allowed, banned = _validate_manifest_paths(manifest)

    template_path = _resolve(Path(manifest.submission_template.path))
    ledger_path = _resolve(Path(manifest.primary_ledger.path))
    case_paths = [_resolve(Path(case.path)) for case in manifest.case_descriptions]

    raw = file_opener.read_bytes(template_path)
    ledger_bytes = file_opener.read_bytes(ledger_path)
    case_bytes = [file_opener.read_bytes(path) for path in case_paths]
    _ = (ledger_bytes, case_bytes)  # read for audit / future ledger use

    _assert_opens_allowlisted(file_opener, allowed=allowed, banned=banned)

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DatasetAdapterError(f"invalid submission template JSON: {exc}") from exc

    if _template_looks_like_scored_answers(payload):
        raise LeakageAttemptError("allowlisted template appears to contain scored answers")

    # Write to a temporary directory; publish only after post-write audit.
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix=".solve-", dir=output_dir))
    try:
        audit = RunFileAudit(run_id=rid)
        audit.record(template_path, component="dataset", purpose="submission_template", data=raw)
        audit.record(ledger_path, component="dataset", purpose="primary_ledger", data=ledger_bytes)
        for case_path, data in zip(case_paths, case_bytes, strict=True):
            audit.record(case_path, component="dataset", purpose="case_description", data=data)

        write_baseline_submission(
            payload,
            tmp_dir,
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
            resolved = _resolve(Path(item.path))
            if item.purpose != "baseline_write" and resolved in banned:
                raise LeakageAttemptError(f"quarantined path in run_manifest: {item.path}")
            if "ground_truth" in Path(item.path).name.lower():
                raise LeakageAttemptError(f"ground truth path in run_manifest: {item.path}")

        _assert_opens_allowlisted(file_opener, allowed=allowed, banned=banned)

        solver_side = {
            "schema_version": manifest.schema_version,
            "submission_template": manifest.submission_template.model_dump(mode="json"),
            "primary_ledger": manifest.primary_ledger.model_dump(mode="json"),
            "case_descriptions": [c.model_dump(mode="json") for c in manifest.case_descriptions],
            "document_count": len(manifest.document_files),
            "quarantined_count": len(manifest.quarantined),
        }
        (tmp_dir / "sanitized_manifest_used.json").write_text(
            json.dumps(solver_side, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        # Rewrite staged artifact paths in run_manifest to the final output location.
        run_manifest_path = tmp_dir / "run_manifest.json"
        run_payload = json.loads(run_manifest_path.read_text(encoding="utf-8"))
        for item in run_payload.get("opened_files", []):
            name = Path(item["path"]).name
            if name in {
                "submission.json",
                "run_manifest.json",
                "unresolved_cells.jsonl",
                "failure_events.jsonl",
                "solver_summary.md",
            }:
                item["path"] = str((output_dir / name).as_posix())
        run_manifest_path.write_text(
            json.dumps(run_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        for name in (
            "submission.json",
            "run_manifest.json",
            "unresolved_cells.jsonl",
            "failure_events.jsonl",
            "solver_summary.md",
            "sanitized_manifest_used.json",
        ):
            src = tmp_dir / name
            if src.is_file():
                os.replace(src, output_dir / name)
    except Exception:
        # Best-effort cleanup of unpublished temp outputs.
        for path in tmp_dir.glob("*"):
            with contextlib.suppress(OSError):
                path.unlink()
        with contextlib.suppress(OSError):
            tmp_dir.rmdir()
        raise
    else:
        with contextlib.suppress(OSError):
            tmp_dir.rmdir()

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
