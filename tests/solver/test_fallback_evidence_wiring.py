"""Fallback submission cells use fallback context for causal evidence replay."""

from __future__ import annotations

from decimal import Decimal

from tests.covenant_evaluation._helpers import _context, _definition, _input, _selector

from halyk_agent.domain.covenant_evaluation import (
    EvaluationExecutor,
    EvaluationManifest,
    EvaluationReport,
    EvaluationStatus,
    plan_definition,
)
from halyk_agent.domain.covenants.ast import MetricCategory, Sum, TransactionSet
from halyk_agent.domain.covenants.quantity import QuantityType, TypedQuantity
from halyk_agent.solver.submission.final import build_final_submission


def test_fallback_result_uses_fallback_context_for_evidence(monkeypatch) -> None:
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
    strict_context = _context(definition, (_input("strict", "50"),))
    fallback_context = _context(definition, (_input("fallback", "125"),))
    plan = plan_definition(definition)
    strict_resolved = EvaluationExecutor().execute(plan, strict_context)
    fallback_result = EvaluationExecutor().execute(plan, fallback_context)
    strict_result = strict_resolved.model_copy(
        update={
            "status": EvaluationStatus.UNRESOLVED,
            "compliance_status": None,
            "actual": None,
        }
    )
    manifest = EvaluationManifest(
        covenant_manifest_hash="c",
        taxonomy_manifest_hash="t",
        calculation_inputs_hash="i",
        selector_coverage_hash="s",
        definition_readiness_hash="d",
        plan_count=1,
        result_count=1,
        resolved_count=0,
        unresolved_count=1,
        error_count=0,
        not_activated_count=0,
        compliant_count=0,
        breach_count=0,
        plans_hash="p",
        results_hash="r",
    )
    report = EvaluationReport(manifest=manifest, plans=(plan,), results=(strict_result,))
    seen: list[object] = []

    def fake_evidence(**kwargs):
        seen.append(kwargs["context"])
        return "TX-fallback"

    monkeypatch.setattr(
        "halyk_agent.solver.submission.final.select_causal_evidence",
        fake_evidence,
    )
    template = {
        "team": "team",
        "contact_email": "team@example.com",
        "model": "model",
        "answers": {"S1": {"6.1": {"status": None, "actual": None, "evidence_txn_id": None}}},
    }
    document, unresolved = build_final_submission(
        template,
        evaluation=report,
        context=strict_context,
        fallback_results={(plan.scenario_id, plan.clause_id): fallback_result},
        fallback_context=fallback_context,
        adjustments=(),
        classified=(),
    )
    assert unresolved == ()
    assert document.answers["S1"]["6.1"].evidence_txn_id == "TX-fallback"
    assert seen == [fallback_context]
