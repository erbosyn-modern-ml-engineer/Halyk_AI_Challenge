"""Shared synthetic contracts for Stage 6 evaluator tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from halyk_agent.domain.covenant_evaluation import EvaluationContext
from halyk_agent.domain.covenants.ast import (
    Constant,
    Divide,
    MetricCategory,
    TransactionSelector,
)
from halyk_agent.domain.covenants.models import (
    Comparator,
    CovenantDefinition,
    CovenantEvidenceRefs,
    CovenantModifier,
    PeriodDefinition,
    PeriodKind,
    ScopeDefinition,
    ScopeKind,
    ScopeProvenance,
)
from halyk_agent.domain.covenants.quantity import QuantityType, TypedQuantity
from halyk_agent.domain.transaction_taxonomy.models import (
    AMOUNT_CONTRACT_VERSION,
    CalculationInput,
    DefinitionReadinessEntry,
    EntityScopeKind,
    InputPeriodSemantics,
    InputSourceKind,
    RelatedPartyStatus,
    SelectorCoverageEntry,
    SelectorReadinessStatus,
)


def _selector(
    category: MetricCategory = MetricCategory.REVENUE,
    *,
    include_flags: tuple[str, ...] = (),
    exclude_flags: tuple[str, ...] = (),
) -> TransactionSelector:
    return TransactionSelector(
        category=category,
        include_flags=include_flags,
        exclude_flags=exclude_flags,
    )


def _definition(
    metric: object,
    *,
    definition_id: str = "def-1",
    comparator: Comparator = Comparator.GTE,
    threshold: TypedQuantity | None = None,
    selectors: tuple[TransactionSelector, ...] = (),
    modifiers: tuple[CovenantModifier, ...] = (),
) -> CovenantDefinition:
    if threshold is None:
        threshold = TypedQuantity(
            quantity_type=QuantityType.MONEY,
            value=Decimal("100"),
            currency="USD",
        )
    return CovenantDefinition(
        definition_id=definition_id,
        scenario_id="S1",
        clause_id="6.1",
        document_id="doc-1",
        document_version_id="docv-1",
        source_file="loan.pdf",
        source_sha256="a" * 64,
        family_id="TEST",
        metric=metric,
        metric_quantity_type=(
            QuantityType.RATIO
            if isinstance(metric, Divide)
            else QuantityType.MONEY
        ),
        comparator=comparator,
        threshold=threshold,
        period=PeriodDefinition(
            period_kind=PeriodKind.CLOSED_INTERVAL,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
        ),
        scope=ScopeDefinition(
            scope_kind=ScopeKind.BORROWER,
            provenance=ScopeProvenance.DEFAULT_BORROWER_BY_RULE,
        ),
        selectors=selectors,
        modifiers=modifiers,
        evidence=CovenantEvidenceRefs(),
        rendered="synthetic test covenant",
    )


def _input(
    input_id: str,
    amount: str,
    *,
    category: MetricCategory = MetricCategory.REVENUE,
    currency: str = "USD",
    transaction_date: date | None = date(2025, 6, 1),
    flags: tuple[str, ...] = (),
    scenario_id: str = "S1",
) -> CalculationInput:
    return CalculationInput(
        input_id=input_id,
        scenario_id=scenario_id,
        source_kind=InputSourceKind.LEDGER_ROW,
        transaction_id=f"TX-{input_id}",
        category=category,
        selector_categories=(category,),
        amount=Decimal(amount),
        source_amount=Decimal(amount),
        metric_amount=Decimal(amount),
        currency=currency,
        period_semantics=InputPeriodSemantics.FLOW,
        transaction_date=transaction_date,
        related_party=RelatedPartyStatus.FALSE,
        entity_scope=EntityScopeKind.BORROWER,
        flags=flags,
    )


def _context(
    definition: CovenantDefinition,
    inputs: tuple[CalculationInput, ...],
    *,
    readiness: SelectorReadinessStatus = SelectorReadinessStatus.READY,
) -> EvaluationContext:
    coverage = tuple(
        SelectorCoverageEntry(
            definition_id=definition.definition_id,
            scenario_id=definition.scenario_id,
            category=selector.category,
            related_party_only=selector.related_party_only,
            group_level=selector.group_level,
            include_flags=selector.include_flags,
            exclude_flags=selector.exclude_flags,
            status=readiness,
            reason_code="OK" if readiness is not SelectorReadinessStatus.UNRESOLVED else "SOURCE",
            matching_input_count=len(inputs),
        )
        for selector in definition.selectors
    )
    return EvaluationContext(
        amount_contract_version=AMOUNT_CONTRACT_VERSION,
        calculation_inputs=inputs,
        selector_coverage=coverage,
        definition_readiness=(
            DefinitionReadinessEntry(
                definition_id=definition.definition_id,
                scenario_id=definition.scenario_id,
                status=(
                    SelectorReadinessStatus.UNRESOLVED
                    if readiness is SelectorReadinessStatus.UNRESOLVED
                    else SelectorReadinessStatus.READY
                ),
                reason_code=(
                    "UNRESOLVED_INPUT"
                    if readiness is SelectorReadinessStatus.UNRESOLVED
                    else "OK"
                ),
            ),
        ),
    )


def _ratio_constant(value: str) -> Constant:
    return Constant(
        quantity=TypedQuantity(
            quantity_type=QuantityType.RATIO,
            value=Decimal(value),
        )
    )


def _ratio_definition(denominator: str) -> CovenantDefinition:
    return _definition(
        Divide(
            numerator=_ratio_constant("1"),
            denominator=_ratio_constant(denominator),
        ),
        threshold=TypedQuantity(
            quantity_type=QuantityType.RATIO,
            value=Decimal("-1"),
        ),
        selectors=(),
    )
