"""Final competition submission construction from deterministic Stage 6 results."""

from __future__ import annotations

import json
import os
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from halyk_agent.domain.covenant_evaluation import (
    CovenantEvaluationResult,
    EvaluationContext,
    EvaluationPlan,
    EvaluationReport,
)
from halyk_agent.domain.transaction_taxonomy.models import AdjustmentEvent, ClassifiedTransaction
from halyk_agent.solver.audit import RunFileAudit
from halyk_agent.solver.errors import SubmissionSchemaError
from halyk_agent.solver.evidence import competition_verdict, select_causal_evidence
from halyk_agent.solver.submission.models import CovenantCell, SubmissionDocument

_TWO_PLACES = Decimal("0.01")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _competition_actual(value: Decimal) -> Decimal:
    """Positive, two-decimal output value; never used for covenant comparison."""

    return abs(value).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


def _json_ready(document: SubmissionDocument) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "team": document.team,
        "contact_email": document.contact_email,
        "model": document.model,
        "answers": {},
    }
    answers: dict[str, dict[str, Any]] = {}
    for scenario_id, cells in document.answers.items():
        answers[scenario_id] = {}
        for clause_id, cell in cells.items():
            actual = cell.actual
            if isinstance(actual, Decimal):
                actual_value: float | int | None = float(actual)
            elif isinstance(actual, (int, float)):
                actual_value = actual
            else:
                actual_value = None
            answers[scenario_id][clause_id] = {
                "status": cell.status.value if cell.status is not None else None,
                "actual": actual_value,
                "evidence_txn_id": cell.evidence_txn_id,
            }
    payload["answers"] = answers
    return payload


def build_final_submission(
    template_payload: dict[str, Any],
    *,
    evaluation: EvaluationReport,
    context: EvaluationContext,
    adjustments: tuple[AdjustmentEvent, ...],
    classified: tuple[ClassifiedTransaction, ...],
    team: str | None = None,
    contact_email: str | None = None,
    model_name: str | None = None,
    fallback_results: dict[tuple[str, str], CovenantEvaluationResult] | None = None,
) -> tuple[SubmissionDocument, tuple[dict[str, Any], ...]]:
    """Fill exactly the template universe; non-evaluable cells remain explicit nulls."""

    try:
        template = SubmissionDocument.model_validate(template_payload)
    except Exception as exc:
        raise SubmissionSchemaError(f"invalid submission template: {exc}") from exc

    result_map = {(item.scenario_id, item.clause_id): item for item in evaluation.results}
    fallback_map = fallback_results or {}
    plan_map: dict[tuple[str, str], EvaluationPlan] = {
        (item.scenario_id, item.clause_id): item for item in evaluation.plans
    }
    template_keys = {
        (scenario_id, clause_id)
        for scenario_id, cells in template.answers.items()
        for clause_id in cells
    }
    if set(result_map) != template_keys or set(plan_map) != template_keys:
        raise SubmissionSchemaError(
            "evaluation universe must exactly match submission template keys"
        )

    answers: dict[str, dict[str, CovenantCell]] = {}
    unresolved: list[dict[str, Any]] = []
    for scenario_id, template_cells in template.answers.items():
        answers[scenario_id] = {}
        for clause_id in template_cells:
            key = (scenario_id, clause_id)
            strict_result = result_map[key]
            plan = plan_map[key]
            result = strict_result
            used_fallback = False
            verdict = competition_verdict(result)
            if (verdict is None or result.actual is None) and key in fallback_map:
                result = fallback_map[key]
                verdict = competition_verdict(result)
                used_fallback = verdict is not None and result.actual is not None
            if verdict is None or result.actual is None:
                answers[scenario_id][clause_id] = CovenantCell()
                unresolved.append(
                    {
                        "scenario_id": scenario_id,
                        "covenant_id": clause_id,
                        "evaluation_status": strict_result.status.value,
                        "activation_state": strict_result.activation_state.value,
                        "reason_codes": [issue.code for issue in strict_result.issues],
                    }
                )
                continue

            evidence = None
            if not used_fallback:
                evidence = select_causal_evidence(
                    plan=plan,
                    result=result,
                    context=context,
                    adjustments=adjustments,
                    classified=classified,
                )
            answers[scenario_id][clause_id] = CovenantCell(
                status=verdict,
                actual=_competition_actual(result.actual.value),
                evidence_txn_id=evidence,
            )

    document = SubmissionDocument(
        team=team if team is not None else template.team,
        contact_email=contact_email if contact_email is not None else template.contact_email,
        model=model_name if model_name is not None else template.model,
        answers=answers,
    )
    if set(document.answers) != set(template.answers):
        raise SubmissionSchemaError("submission scenario keys differ from template")
    for scenario_id in template.answers:
        if set(document.answers[scenario_id]) != set(template.answers[scenario_id]):
            raise SubmissionSchemaError(
                f"submission covenant keys differ from template for {scenario_id}"
            )
    return document, tuple(unresolved)


def write_final_submission(
    document: SubmissionDocument,
    output_dir: Path,
    *,
    audit: RunFileAudit,
    unresolved: tuple[dict[str, Any], ...] = (),
) -> dict[str, Path]:
    """Publish deterministic JSON plus unresolved diagnostics into a staged directory."""

    output_dir.mkdir(parents=True, exist_ok=True)
    submission_path = output_dir / "submission.json"
    payload = _json_ready(document)
    text = (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=False,
            allow_nan=False,
        )
        + "\n"
    )
    _atomic_write(submission_path, text)
    audit.record(
        submission_path,
        component="submission",
        purpose="submission_write",
        data=text.encode("utf-8"),
    )
    unresolved_path = output_dir / "unresolved_cells.jsonl"
    unresolved_text = "\n".join(
        json.dumps(item, ensure_ascii=False, sort_keys=True) for item in unresolved
    )
    if unresolved_text:
        unresolved_text += "\n"
    _atomic_write(unresolved_path, unresolved_text)
    return {"submission": submission_path, "unresolved": unresolved_path}
