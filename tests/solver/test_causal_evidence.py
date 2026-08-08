"""Regression tests for treatment-causal submission evidence."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from halyk_agent.domain.covenant_evaluation import (
    EvaluationContext,
    EvaluationExecutor,
    EvaluationValidationError,
    plan_definition,
)
from halyk_agent.domain.covenants.ast import (
    MetricCategory,
    Sum,
    TransactionSelector,
    TransactionSet,
)
from halyk_agent.domain.covenants.models import (
    Comparator,
    CovenantDefinition,
    CovenantEvidenceRefs,
    PeriodDefinition,
    PeriodKind,
    ScopeDefinition,
    ScopeKind,
    ScopeProvenance,
)
from halyk_agent.domain.covenants.quantity import QuantityType, TypedQuantity
from halyk_agent.domain.transaction_taxonomy.models import (
    AMOUNT_CONTRACT_VERSION,
    AdjustmentEvent,
    AdjustmentEventType,
    CalculationInput,
    ClassificationMethod,
    ClassificationStatus,
    ClassifiedTransaction,
    DefinitionReadinessEntry,
    EntityScopeKind,
    InputPeriodSemantics,
    InputSourceKind,
    RelatedPartyStatus,
    SelectorCoverageEntry,
    SelectorReadinessStatus,
)
from halyk_agent.solver.evidence import select_causal_evidence


def _definition(selector: TransactionSelector) -> CovenantDefinition:
    return CovenantDefinition(
        definition_id="def-1",
        scenario_id="S1",
        clause_id="6.1",
        document_id="doc-1",
        document_version_id="docv-1",
        source_file="loan.pdf",
        source_sha256="a" * 64,
        family_id="MIN_REVENUE",
        metric=Sum(of=TransactionSet(selector=selector)),
        metric_quantity_type=QuantityType.MONEY,
        comparator=Comparator.GTE,
        threshold=TypedQuantity(
            quantity_type=QuantityType.MONEY,
            value=Decimal("100"),
            currency="USD",
        ),
        period=PeriodDefinition(
            period_kind=PeriodKind.CLOSED_INTERVAL,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
        ),
        scope=ScopeDefinition(
            scope_kind=ScopeKind.BORROWER,
            provenance=ScopeProvenance.DEFAULT_BORROWER_BY_RULE,
        ),
        selectors=(selector,),
        evidence=CovenantEvidenceRefs(),
        rendered="synthetic revenue covenant",
    )


def _input(input_id: str, amount: str, *, scenario_id: str) -> CalculationInput:
    return CalculationInput(
        input_id=input_id,
        scenario_id=scenario_id,
        source_kind=InputSourceKind.LEDGER_ROW,
        transaction_id=f"TX-{input_id}",
        category=MetricCategory.REVENUE,
        selector_categories=(MetricCategory.REVENUE,),
        amount=Decimal(amount),
        source_amount=Decimal(amount),
        metric_amount=Decimal(amount),
        currency="USD",
        period_semantics=InputPeriodSemantics.FLOW,
        transaction_date=date(2025, 6, 1),
        related_party=RelatedPartyStatus.FALSE,
        entity_scope=EntityScopeKind.BORROWER,
    )


def test_amount_correction_is_causal_inside_shared_multi_definition_context() -> None:
    selector = TransactionSelector(category=MetricCategory.REVENUE)
    definition = _definition(selector)
    current_input = _input("i1", "120", scenario_id="S1").model_copy(
        update={"applied_fact_ids": ("fact-correction",)}
    )
    coverage = SelectorCoverageEntry(
        definition_id=definition.definition_id,
        scenario_id=definition.scenario_id,
        category=selector.category,
        related_party_only=False,
        group_level=False,
        status=SelectorReadinessStatus.READY,
        reason_code="OK",
        matching_input_count=1,
    )
    readiness = DefinitionReadinessEntry(
        definition_id=definition.definition_id,
        scenario_id=definition.scenario_id,
        status=SelectorReadinessStatus.READY,
        reason_code="OK",
    )
    single_context = EvaluationContext(
        amount_contract_version=AMOUNT_CONTRACT_VERSION,
        calculation_inputs=(current_input,),
        selector_coverage=(coverage,),
        definition_readiness=(readiness,),
    )
    plan = plan_definition(definition)
    result = EvaluationExecutor().execute(plan, single_context)

    foreign_input = _input("foreign", "1", scenario_id="S2")
    shared_context = single_context.model_copy(
        update={
            "calculation_inputs": (*single_context.calculation_inputs, foreign_input),
            "selector_coverage": (
                *single_context.selector_coverage,
                coverage.model_copy(update={"definition_id": "def-2", "scenario_id": "S2"}),
            ),
            "definition_readiness": (
                *single_context.definition_readiness,
                readiness.model_copy(update={"definition_id": "def-2", "scenario_id": "S2"}),
            ),
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

    evidence = select_causal_evidence(
        plan=plan,
        result=result,
        context=shared_context,
        adjustments=(adjustment,),
        classified=(classified,),
    )

    assert evidence == "TX-i1"


def test_amount_correction_from_absent_amount_removes_input_in_counterfactual() -> None:
    selector = TransactionSelector(category=MetricCategory.REVENUE)
    definition = _definition(selector)
    current_input = _input("off", "120", scenario_id="S1").model_copy(
        update={"applied_fact_ids": ("fact-off-ledger",)}
    )
    coverage = SelectorCoverageEntry(
        definition_id=definition.definition_id,
        scenario_id=definition.scenario_id,
        category=selector.category,
        related_party_only=False,
        group_level=False,
        status=SelectorReadinessStatus.READY,
        reason_code="OK",
        matching_input_count=1,
    )
    readiness = DefinitionReadinessEntry(
        definition_id=definition.definition_id,
        scenario_id=definition.scenario_id,
        status=SelectorReadinessStatus.READY,
        reason_code="OK",
    )
    context = EvaluationContext(
        amount_contract_version=AMOUNT_CONTRACT_VERSION,
        calculation_inputs=(current_input,),
        selector_coverage=(coverage,),
        definition_readiness=(readiness,),
    )
    plan = plan_definition(definition)
    result = EvaluationExecutor().execute(plan, context)
    adjustment = AdjustmentEvent(
        event_id="event-off-ledger",
        event_type=AdjustmentEventType.AMOUNT_CORRECTION,
        scenario_id="S1",
        fact_id="fact-off-ledger",
        transaction_id="TX-off",
        before={"effective_amount": None, "currency": "USD"},
        after={"effective_amount": "120", "currency": "USD"},
        reason_code="AUTHORITATIVE_AMOUNT_CORRECTION",
    )
    classified = ClassifiedTransaction(
        transaction_id="TX-off",
        source_ledger="ledger.csv",
        source_row_index=1,
        source_sha256="b" * 64,
        scenario_id="S1",
        account_id="REV-1",
        original_amount=None,
        original_currency="USD",
        effective_amount=Decimal("120"),
        effective_currency="USD",
        original_date=date(2025, 6, 1),
        original_category=selector.category,
        effective_category=selector.category,
        counterparty_raw="Customer LLC",
        description="Off-ledger revenue correction",
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
        == "TX-off"
    )
