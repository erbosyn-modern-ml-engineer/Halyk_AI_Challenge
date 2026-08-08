"""Regression tests for treatment-causal submission evidence."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from covenant_evaluation._helpers import _context, _definition, _input, _selector
from halyk_agent.domain.covenant_evaluation import (
    EvaluationExecutor,
    EvaluationValidationError,
    plan_definition,
)
from halyk_agent.domain.covenants.ast import Sum, TransactionSet
from halyk_agent.domain.covenants.quantity import QuantityType, TypedQuantity
from halyk_agent.domain.transaction_taxonomy.models import (
    AdjustmentEvent,
    AdjustmentEventType,
    ClassificationMethod,
    ClassificationStatus,
    ClassifiedTransaction,
)
from halyk_agent.solver.evidence import select_causal_evidence


def test_amount_correction_is_causal_inside_shared_multi_definition_context() -> None:
    selector = _selector()
    definition = _definition(
        Sum(of=TransactionSet(selector=selector)),
        selectors=(selector,),
        threshold=TypedQuantity(
            quantity_type=QuantityType.MONEY,
            value=Decimal("100"),
            currency="USD",
        ),
    )
    current_input = _input("i1", "120").model_copy(
        update={"applied_fact_ids": ("fact-correction",)}
    )
    single_context = _context(definition, (current_input,))
    plan = plan_definition(definition)
    result = EvaluationExecutor().execute(plan, single_context)

    foreign_input = _input("foreign", "1", scenario_id="S2")
    foreign_coverage = single_context.selector_coverage[0].model_copy(
        update={"definition_id": "def-2", "scenario_id": "S2"}
    )
    foreign_readiness = single_context.definition_readiness[0].model_copy(
        update={"definition_id": "def-2", "scenario_id": "S2"}
    )
    shared_context = single_context.model_copy(
        update={
            "calculation_inputs": (*single_context.calculation_inputs, foreign_input),
            "selector_coverage": (*single_context.selector_coverage, foreign_coverage),
            "definition_readiness": (*single_context.definition_readiness, foreign_readiness),
        }
    )

    # This is the production shape: one shared Stage 5F universe cannot be passed
    # directly to a single-plan executor without covenant/scenario scoping.
    with pytest.raises(EvaluationValidationError):
        EvaluationExecutor().execute(plan, shared_context)

    adjustment = AdjustmentEvent(
        event_id="event-correction",
        event_type=AdjustmentEventType.AMOUNT_CORRECTION,
        scenario_id="S1",
        fact_id="fact-correction",
        transaction_id="TX-i1",
        before={"effective_amount": "80", "currency": "USD"},
        after={"effective_amount": "120", "currency": "USD"},
        reason_code="AUTHORITATIVE_AMOUNT_CORRECTION",
    )
    classified = ClassifiedTransaction(
        transaction_id="TX-i1",
        source_ledger="ledger.csv",
        source_row_index=1,
        source_sha256="a" * 64,
        scenario_id="S1",
        account_id="REV-1",
        original_amount=Decimal("80"),
        original_currency="USD",
        effective_amount=Decimal("120"),
        effective_currency="USD",
        original_date=date(2025, 6, 1),
        original_category=selector.category,
        effective_category=selector.category,
        counterparty_raw="Customer LLC",
        description="Revenue receipt",
        classification_status=ClassificationStatus.CLASSIFIED,
        classification_method=ClassificationMethod.AUTHORITATIVE_RECLASSIFICATION,
    )

    evidence = select_causal_evidence(
        plan=plan,
        result=result,
        context=shared_context,
        adjustments=(adjustment,),
        classified=(classified,),
    )

    assert evidence == "TX-i1"
