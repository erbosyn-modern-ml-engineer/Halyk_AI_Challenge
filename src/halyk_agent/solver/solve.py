"""Competition solve orchestration — sanitized manifest only (no raw dataset root)."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import uuid
from pathlib import Path

from halyk_agent.dataset_access import LeakageAttemptError as SharedLeakageAttemptError
from halyk_agent.dataset_access import (
    assert_opens_allowlisted as shared_assert_opens_allowlisted,
)
from halyk_agent.dataset_access import (
    resolve_dataset_path,
)
from halyk_agent.dataset_access import (
    validate_manifest_paths as shared_validate_manifest_paths,
)
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
    return resolve_dataset_path(path)


def _validate_manifest_paths(manifest: SanitizedDatasetManifest) -> tuple[set[Path], set[Path]]:
    try:
        return shared_validate_manifest_paths(manifest)
    except SharedLeakageAttemptError as exc:
        raise LeakageAttemptError(exc.message) from exc


def _assert_opens_allowlisted(
    opener: FileOpener,
    *,
    allowed: set[Path],
    banned: set[Path],
) -> None:
    try:
        shared_assert_opens_allowlisted(opener, allowed=allowed, banned=banned)
    except SharedLeakageAttemptError as exc:
        raise LeakageAttemptError(exc.message) from exc


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


def solve_competition_from_manifest(
    manifest: SanitizedDatasetManifest,
    output_dir: Path,
    *,
    team: str | None = None,
    contact_email: str | None = None,
    model_name: str | None = None,
    run_id: str | None = None,
    opener: FileOpener | None = None,
) -> dict[str, str]:
    """Stage 7 real solver: sanitized sources -> deterministic pipeline -> submission.

    The raw dataset root is never passed into this service.  Every source read is
    constrained by ``SanitizedDatasetManifest`` and the audited opener; downstream
    stages operate only on verified private copies inside the run workspace.
    """

    from halyk_agent.adapters.archive.hashing import sha256_file
    from halyk_agent.solver.pipeline import run_competition_pipeline
    from halyk_agent.solver.submission.final import build_final_submission, write_final_submission

    rid = run_id or uuid.uuid4().hex
    file_opener = require_audited_opener(opener or RecordingFileOpener())
    allowed, banned = _validate_manifest_paths(manifest)

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if any(output_dir.iterdir()):
        raise DatasetAdapterError(f"output directory must be empty: {output_dir}")
    workspace = Path(tempfile.mkdtemp(prefix=".solve-real-", dir=output_dir))
    public_stage = workspace / "public"
    public_stage.mkdir(parents=True, exist_ok=True)
    audit = RunFileAudit(run_id=rid)

    try:
        pipeline = run_competition_pipeline(
            manifest,
            workspace=workspace / "pipeline",
            opener=file_opener,
            audit=audit,
        )
        _assert_opens_allowlisted(file_opener, allowed=allowed, banned=banned)
        audit.assert_no_ground_truth()

        from halyk_agent.solver.fallbacks import build_competitive_fallbacks

        fallback = build_competitive_fallbacks(
            evaluation=pipeline.evaluation,
            context=pipeline.context,
            adjustments=pipeline.taxonomy.adjustments,
            parsed_dir=pipeline.parsed_dir,
            routing_dir=pipeline.routing_dir,
        )
        document, unresolved = build_final_submission(
            pipeline.materialized.template_payload,
            evaluation=pipeline.evaluation,
            context=pipeline.context,
            adjustments=pipeline.taxonomy.adjustments,
            classified=pipeline.taxonomy.classified,
            team=team,
            contact_email=contact_email,
            model_name=model_name,
            fallback_results=fallback.results,
        )
        written = write_final_submission(
            document,
            public_stage,
            audit=audit,
            unresolved=unresolved,
        )
        audit.assert_no_ground_truth()

        opened_files = []
        for item in audit.files:
            Path(item.path)
            purpose = item.purpose
            if purpose not in {
                "submission_template",
                "primary_ledger",
                "case_description",
                "document",
                "submission_write",
            }:
                raise LeakageAttemptError(f"unexpected real-solver purpose: {purpose}")
            published_path = item.path
            if purpose == "submission_write":
                published_path = (output_dir / "submission.json").as_posix()
            opened_files.append(
                {
                    **item.model_dump(mode="json"),
                    "path": published_path,
                }
            )

        fallback_path = public_stage / "fallback_cells.jsonl"
        fallback_text = "\n".join(
            json.dumps(item, ensure_ascii=False, sort_keys=True) for item in fallback.diagnostics
        )
        if fallback_text:
            fallback_text += "\n"
        fallback_path.write_text(fallback_text, encoding="utf-8", newline="\n")

        pipeline_manifest = {
            "schema_version": "halyk.competition_pipeline.v2",
            "run_id": rid,
            "stage_summary": pipeline.stage_summary,
            "source_read_count": sum(
                1 for item in audit.files if item.purpose != "submission_write"
            ),
            "ground_truth_access": "none",
            "evaluation_manifest_sha256": sha256_file(
                pipeline.evaluation_dir / "evaluation_manifest.json"
            ),
            "submission_sha256": sha256_file(written["submission"]),
            "unresolved_cell_count": len(unresolved),
            "fallback_cell_count": len(fallback.results),
            "fallback_eur_usd_rate": (
                str(fallback.eur_usd_rate) if fallback.eur_usd_rate is not None else None
            ),
        }
        (public_stage / "pipeline_manifest.json").write_text(
            json.dumps(pipeline_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        run_manifest = {
            "schema_version": "halyk.competition_run.v1",
            "run_id": rid,
            "opened_files": opened_files,
            "stage_summary": pipeline.stage_summary,
            "unresolved_cell_count": len(unresolved),
            "fallback_cell_count": len(fallback.results),
            "ground_truth_access": "none",
        }
        (public_stage / "run_manifest.json").write_text(
            json.dumps(run_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        summary_lines = [
            "# Solver summary",
            "",
            f"- run_id: `{rid}`",
            f"- cells: {sum(len(cells) for cells in document.answers.values())}",
            f"- unresolved_cells: {len(unresolved)}",
            f"- fallback_cells: {len(fallback.results)}",
            f"- evaluation_resolved: {pipeline.evaluation.manifest.resolved_count}",
            f"- evaluation_unresolved: {pipeline.evaluation.manifest.unresolved_count}",
            f"- evaluation_errors: {pipeline.evaluation.manifest.error_count}",
            "- ground_truth_access: none",
            "",
        ]
        (public_stage / "solver_summary.md").write_text(
            "\n".join(summary_lines), encoding="utf-8", newline="\n"
        )
        evaluation_summary = pipeline.evaluation_dir / "evaluation_summary.md"
        if evaluation_summary.is_file():
            (public_stage / "evaluation_summary.md").write_bytes(evaluation_summary.read_bytes())

        for child in sorted(public_stage.iterdir()):
            os.replace(child, output_dir / child.name)
    except Exception:
        for child in list(output_dir.iterdir()):
            if child == workspace:
                continue
            with contextlib.suppress(OSError):
                if child.is_dir():
                    import shutil

                    shutil.rmtree(child)
                else:
                    child.unlink()
        raise
    finally:
        import shutil

        shutil.rmtree(workspace, ignore_errors=True)

    return {
        "submission": str((output_dir / "submission.json").as_posix()),
        "run_manifest": str((output_dir / "run_manifest.json").as_posix()),
        "summary": str((output_dir / "solver_summary.md").as_posix()),
        "pipeline_manifest": str((output_dir / "pipeline_manifest.json").as_posix()),
        "run_id": rid,
    }
