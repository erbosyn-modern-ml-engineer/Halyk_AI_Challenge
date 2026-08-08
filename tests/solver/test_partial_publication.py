"""A covenant compile failure must cost one cell, never the whole submission."""

from __future__ import annotations

from decimal import Decimal

import pytest
from tests.covenant_evaluation._helpers import _context, _definition, _input, _selector

from halyk_agent.domain.covenant_evaluation import (
    EvaluationExecutor,
    EvaluationManifest,
    EvaluationReport,
    plan_definition,
)
from halyk_agent.domain.covenants.ast import MetricCategory, Sum, TransactionSet
from halyk_agent.domain.covenants.models import CompileStatus, CovenantCompileFailure
from halyk_agent.domain.covenants.quantity import QuantityType, TypedQuantity
from halyk_agent.solver.errors import SubmissionSchemaError
from halyk_agent.solver.submission.final import build_final_submission


def _evaluation() -> tuple[EvaluationReport, object]:
    selector = _selector(MetricCategory.REVENUE)
    definition = _definition(
        Sum(of=TransactionSet(selector=selector)),
        selectors=(selector,),
        threshold=TypedQuantity(
            quantity_type=QuantityType.MONEY,
            value=Decimal("100"),
            currency="USD",
        ),
    )
    context = _context(definition, (_input("i1", "125"),))
    plan = plan_definition(definition)
    result = EvaluationExecutor().execute(plan, context)
    manifest = EvaluationManifest(
        covenant_manifest_hash="c",
        taxonomy_manifest_hash="t",
        calculation_inputs_hash="i",
        selector_coverage_hash="s",
        definition_readiness_hash="d",
        plan_count=1,
        result_count=1,
        resolved_count=1,
        unresolved_count=0,
        error_count=0,
        not_activated_count=0,
        compliant_count=1,
        breach_count=0,
        plans_hash="p",
        results_hash="r",
    )
    return EvaluationReport(manifest=manifest, plans=(plan,), results=(result,)), context


def _template() -> dict[str, object]:
    empty = {"status": None, "actual": None, "evidence_txn_id": None}
    return {
        "team": "team",
        "contact_email": "team@example.com",
        "model": "model",
        "answers": {"S1": {"6.1": dict(empty), "6.2": dict(empty)}},
    }


def test_compile_failure_is_published_as_one_explicit_null_cell() -> None:
    evaluation, context = _evaluation()
    failure = CovenantCompileFailure(
        failure_id="failure-1",
        scenario_id="S1",
        clause_id="6.2",
        status=CompileStatus.MALFORMED_THRESHOLD,
        reason="synthetic malformed threshold",
    )
    document, unresolved = build_final_submission(
        _template(),
        evaluation=evaluation,
        context=context,
        adjustments=(),
        classified=(),
        compile_failures=(failure,),
    )
    assert document.answers["S1"]["6.1"].status is not None
    failed = document.answers["S1"]["6.2"]
    assert failed.status is None
    assert failed.actual is None
    assert failed.evidence_txn_id is None
    assert unresolved == (
        {
            "scenario_id": "S1",
            "covenant_id": "6.2",
            "evaluation_status": "COMPILE_FAILURE",
            "activation_state": "NOT_APPLICABLE",
            "reason_codes": ["MALFORMED_THRESHOLD"],
            "compile_reason": "synthetic malformed threshold",
        },
    )


def test_missing_result_without_compile_failure_remains_a_hard_integrity_error() -> None:
    evaluation, context = _evaluation()
    with pytest.raises(SubmissionSchemaError, match="compile failures"):
        build_final_submission(
            _template(),
            evaluation=evaluation,
            context=context,
            adjustments=(),
            classified=(),
        )
