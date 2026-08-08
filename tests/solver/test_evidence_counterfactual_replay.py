"""Counterfactual evidence must cover both treatments and plain contributing rows."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from tests.solver.test_causal_evidence import _definition, _input

from halyk_agent.domain.covenant_evaluation import (
    EvaluationContext,
    EvaluationExecutor,
    plan_definition,
)
from halyk_agent.domain.covenants.ast import (
    Constant,
    Divide,
    MetricCategory,
    Sum,
    TransactionSelector,
    TransactionSet,
)
from halyk_agent.domain.covenants.quantity import QuantityType, TypedQuantity
from halyk_agent.domain.transaction_taxonomy.models import (
    AMOUNT_CONTRACT_VERSION,
    AdjustmentEvent,
    AdjustmentEventType,
    ClassificationMethod,
    ClassificationStatus,
    ClassifiedTransaction,
    DefinitionReadinessEntry,
    SelectorCoverageEntry,
    SelectorReadinessStatus,
)
from halyk_agent.solver.evidence import select_causal_evidence


def _context(definition, selector: TransactionSelector, *inputs):
    coverage = SelectorCoverageEntry(
        definition_id=definition.definition_id,
        scenario_id=definition.scenario_id,
        category=selector.category,
        related_party_only=False,
        group_level=False,
        status=SelectorReadinessStatus.READY,
        reason_code="OK",
        matching_input_count=len(inputs),
    )
    readiness = DefinitionReadinessEntry(
        definition_id=definition.definition_id,
        scenario_id=definition.scenario_id,
        status=SelectorReadinessStatus.READY,
        reason_code="OK",
    )
    return EvaluationContext(
        amount_contract_version=AMOUNT_CONTRACT_VERSION,
        calculation_inputs=tuple(inputs),
        selector_coverage=(coverage,),
        definition_readiness=(readiness,),
    )


def test_plain_contributing_transaction_is_causal_without_adjustment_event() -> None:
    selector = TransactionSelector(category=MetricCategory.REVENUE)
    definition = _definition(selector)
    context = _context(definition, selector, _input("plain", "120", scenario_id="S1"))
    plan = plan_definition(definition)
    result = EvaluationExecutor().execute(plan, context)

    assert (
        select_causal_evidence(
            plan=plan,
            result=result,
            context=context,
            adjustments=(),
            classified=(),
        )
        == "TX-plain"
    )


def test_ratio_metric_can_publish_unique_authoritative_treatment_cause() -> None:
    selector = TransactionSelector(category=MetricCategory.REVENUE)
    definition = _definition(selector).model_copy(
        update={
            "family_id": "REVENUE_RATIO",
            "metric": Divide(
                numerator=Sum(of=TransactionSet(selector=selector)),
                denominator=Constant(
                    quantity=TypedQuantity(
                        quantity_type=QuantityType.MONEY,
                        value=Decimal("100"),
                        currency="USD",
                    )
                ),
            ),
            "metric_quantity_type": QuantityType.RATIO,
            "threshold": TypedQuantity(
                quantity_type=QuantityType.RATIO,
                value=Decimal("1"),
            ),
        }
    )
    current_input = _input("ratio", "120", scenario_id="S1").model_copy(
        update={"applied_fact_ids": ("fact-correction",)}
    )
    context = _context(definition, selector, current_input)
    plan = plan_definition(definition)
    result = EvaluationExecutor().execute(plan, context)
    adjustment = AdjustmentEvent(
        event_id="event-correction",
        event_type=AdjustmentEventType.AMOUNT_CORRECTION,
        scenario_id="S1",
        fact_id="fact-correction",
        transaction_id="TX-ratio",
        before={"effective_amount": "80", "currency": "USD"},
        after={"effective_amount": "120", "currency": "USD"},
        reason_code="AUTHORITATIVE_AMOUNT_CORRECTION",
    )
    classified = ClassifiedTransaction(
        transaction_id="TX-ratio",
        source_ledger="ledger.csv",
        source_row_index=1,
        source_sha256="b" * 64,
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

    assert (
        select_causal_evidence(
            plan=plan,
            result=result,
            context=context,
            adjustments=(adjustment,),
            classified=(classified,),
        )
        == "TX-ratio"
    )


def test_multiple_plain_transaction_removals_are_not_unique_evidence() -> None:
    selector = TransactionSelector(category=MetricCategory.REVENUE)
    definition = _definition(selector)
    context = _context(
        definition,
        selector,
        _input("a", "60", scenario_id="S1"),
        _input("b", "60", scenario_id="S1"),
    )
    plan = plan_definition(definition)
    result = EvaluationExecutor().execute(plan, context)

    assert (
        select_causal_evidence(
            plan=plan,
            result=result,
            context=context,
            adjustments=(),
            classified=(),
        )
        is None
    )
