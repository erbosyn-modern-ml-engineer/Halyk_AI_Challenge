"""Deterministic schema-valid baseline submission writer."""

from __future__ import annotations

import json
import os
from enum import StrEnum
from pathlib import Path
from typing import Any

from halyk_agent.solver.audit import RunFileAudit
from halyk_agent.solver.errors import SubmissionSchemaError
from halyk_agent.solver.failures import FailureEvent
from halyk_agent.solver.submission.models import CovenantCell, SubmissionDocument


class UnresolvedCellPolicy(StrEnum):
    """Declared unresolved policy: keep null fields for every template cell."""

    NULL_FIELDS = "NULL_FIELDS"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"


def write_baseline_submission(
    template_payload: dict[str, Any],
    output_dir: Path,
    *,
    run_id: str,
    audit: RunFileAudit,
    team: str | None = None,
    contact_email: str | None = None,
    model_name: str | None = None,
    failure_events: list[FailureEvent] | None = None,
    policy: UnresolvedCellPolicy = UnresolvedCellPolicy.NULL_FIELDS,
) -> SubmissionDocument:
    """Create a complete schema-valid unresolved baseline from the template."""
    if policy is not UnresolvedCellPolicy.NULL_FIELDS:
        raise SubmissionSchemaError(f"unsupported unresolved policy: {policy}")

    try:
        template = SubmissionDocument.model_validate(template_payload)
    except Exception as exc:
        raise SubmissionSchemaError(f"invalid submission template: {exc}") from exc

    answers: dict[str, dict[str, CovenantCell]] = {}
    unresolved_lines: list[str] = []
    for scenario_id in sorted(template.answers):
        covenants = template.answers[scenario_id]
        answers[scenario_id] = {}
        for covenant_id in sorted(covenants):
            answers[scenario_id][covenant_id] = CovenantCell(
                status=None,
                actual=None,
                evidence_txn_id=None,
            )
            unresolved_lines.append(
                json.dumps(
                    {
                        "scenario_id": scenario_id,
                        "covenant_id": covenant_id,
                        "reason_code": "BASELINE_UNRESOLVED",
                        "policy": policy.value,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )

    document = SubmissionDocument(
        team=team if team is not None else template.team,
        contact_email=contact_email if contact_email is not None else template.contact_email,
        model=model_name if model_name is not None else template.model,
        answers=answers,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    submission_path = output_dir / "submission.json"
    payload = document.model_dump(mode="json")
    text = _dump(payload)
    _atomic_write(submission_path, text)
    audit.record(
        submission_path, component="submission", purpose="baseline_write", data=text.encode("utf-8")
    )

    manifest = {
        "run_id": run_id,
        "unresolved_policy": policy.value,
        "opened_files": [item.model_dump(mode="json") for item in audit.files],
        "scenario_count": len(document.answers),
        "cell_count": sum(len(v) for v in document.answers.values()),
    }
    _atomic_write(output_dir / "run_manifest.json", _dump(manifest))
    _atomic_write(
        output_dir / "unresolved_cells.jsonl",
        ("\n".join(unresolved_lines) + "\n") if unresolved_lines else "",
    )
    events = failure_events or []
    _atomic_write(
        output_dir / "failure_events.jsonl",
        (
            "\n".join(
                json.dumps(e.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
                for e in events
            )
            + ("\n" if events else "")
        ),
    )
    summary = "\n".join(
        [
            "# Solver summary",
            "",
            f"- run_id: `{run_id}`",
            f"- unresolved_policy: `{policy.value}`",
            f"- scenarios: {len(document.answers)}",
            f"- cells: {sum(len(v) for v in document.answers.values())}",
            "- ground_truth_access: none (competition baseline)",
            "",
        ]
    )
    _atomic_write(output_dir / "solver_summary.md", summary)
    return document
