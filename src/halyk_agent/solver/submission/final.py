"""Final competition submission construction from deterministic Stage 6 results."""

from __future__ import annotations

import json
import os
from decimal import ROUND_HALF_UP, Context, Decimal, localcontext
from pathlib import Path
from typing import Any

from halyk_agent.domain.covenant_evaluation import (
    CovenantEvaluationResult,
    EvaluationContext,
    EvaluationReport,
)
from halyk_agent.domain.covenant_evaluation.constants import DECIMAL_PRECISION
from halyk_agent.domain.covenants.models import CovenantCompileFailure
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
    """Positive, two-decimal output value; never used for covenant comparison.

    Submission formatting owns an explicit Decimal context just like Stage 6.
    Ambient process precision/rounding must never turn a valid large amount into
    ``InvalidOperation`` or change its serialized value.
    """

    context = Context(prec=DECIMAL_PRECISION, rounding=ROUND_HALF_UP)
    with localcontext(context):
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
    compile_failures: tuple[CovenantCompileFailure, ...] = (),
) -> tuple[SubmissionDocument, tuple[dict[str, Any], ...]]:
    """Fill exactly the template universe; non-evaluable cells remain explicit nulls."""

    fallback_results = fallback_results or {}
    template_answers = template_payload.get("answers")
    if not isinstance(template_answers, dict):
        raise SubmissionSchemaError("template answers must be an object")

    template_keys: set[tuple[str, str]] = set()
    for scenario_id, cells in template_answers.items():
        if not isinstance(scenario_id, str) or not isinstance(cells, dict):
            raise SubmissionSchemaError("invalid template answers shape")
        for clause_id in cells:
            if not isinstance(clause_id, str):
                raise SubmissionSchemaError("invalid covenant ID in template")
            template_keys.add((scenario_id, clause_id))

    result_map = {(item.scenario_id, item.clause_id): item for item in evaluation.results}
    plan_map = {(item.scenario_id, item.clause_id): item for item in evaluation.plans}
    result_keys = set(result_map)
    plan_keys = set(plan_map)
    if result_keys != plan_keys:
        raise SubmissionSchemaError("evaluation result/plan universes differ")
    if not result_keys.issubset(template_keys):
        raise SubmissionSchemaError("evaluation contains keys outside submission template")
    failure_map: dict[tuple[str, str], CovenantCompileFailure] = {}
    for failure in compile_failures:
        key = (failure.scenario_id, failure.clause_id)
        if key in failure_map:
            raise SubmissionSchemaError(f"duplicate compile failure for {key}")
        failure_map[key] = failure
    if set(failure_map) & result_keys:
        raise SubmissionSchemaError("compiled results overlap covenant compile failures")
    missing_keys = template_keys - result_keys
    if set(failure_map) != missing_keys:
        raise SubmissionSchemaError(
            "missing evaluation keys must exactly match covenant compile failures"
        )

    answers: dict[str, dict[str, CovenantCell]] = {}
    unresolved: list[dict[str, Any]] = []
    for scenario_id, cells in template_answers.items():
        answers[scenario_id] = {}
        assert isinstance(cells, dict)
        for clause_id in cells:
            key = (scenario_id, clause_id)
            if key not in result_map:
                failure = failure_map[key]
                answers[scenario_id][clause_id] = CovenantCell()
                unresolved.append(
                    {
                        "scenario_id": scenario_id,
                        "covenant_id": clause_id,
                        "evaluation_status": "COMPILE_FAILURE",
                        "activation_state": "NOT_APPLICABLE",
                        "reason_codes": [failure.status.value],
                        "compile_reason": failure.reason,
                    }
                )
                continue
            strict_result = result_map[key]
            plan = plan_map[key]
            selected = strict_result
            used_fallback = False
            if key in fallback_results:
                selected = fallback_results[key]
                used_fallback = True

            status, actual_number = competition_verdict(plan, selected)
            if status is None or actual_number is None:
                answers[scenario_id][clause_id] = CovenantCell()
                unresolved.append(
                    {
                        "scenario_id": scenario_id,
                        "covenant_id": clause_id,
                        "evaluation_status": selected.status.value,
                        "activation_state": selected.activation_state.value,
                        "reason_codes": [issue.code for issue in selected.issues],
                    }
                )
                continue

            evidence: str | None = None
            if not used_fallback:
                evidence = select_causal_evidence(
                    plan=plan,
                    result=selected,
                    context=context,
                    adjustments=adjustments,
                    classified=classified,
                )

            answers[scenario_id][clause_id] = CovenantCell(
                status=status,
                actual=_competition_actual(actual_number.value),
                evidence_txn_id=evidence,
            )

    document = SubmissionDocument(
        team=team or str(template_payload.get("team") or ""),
        contact_email=contact_email or str(template_payload.get("contact_email") or ""),
        model=model_name or str(template_payload.get("model") or ""),
        answers=answers,
    )
    return document, tuple(unresolved)


def write_final_submission(
    path: Path,
    document: SubmissionDocument,
    *,
    file_audit: RunFileAudit,
) -> None:
    """Write the validated template-shaped final submission atomically."""

    payload = _json_ready(document)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    _atomic_write(path, text)
    file_audit.record_write(path)
