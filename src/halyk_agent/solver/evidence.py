"""Causal transaction evidence selection for competition submissions.

Evidence is intentionally conservative. A ledger transaction is publishable only
when an executable counterfactual intervention changes the final binary verdict.
Authoritative Stage 5F treatments are replayed first; otherwise the engine performs
a fixed-context leave-one-contributing-transaction-out replay. Mere size, recency
or threshold-crossing order is never evidence.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from halyk_agent.domain.covenant_evaluation import (
    ComplianceStatus,
    CovenantEvaluationResult,
    EvaluationContext,
    EvaluationExecutor,
    EvaluationPlan,
    EvaluationStatus,
    EvaluationValidationError,
)
from halyk_agent.domain.covenants.ast import MetricCategory
from halyk_agent.domain.transaction_taxonomy.amounts import (
    metric_amount_from_source,
    sign_contract_for_category,
)
from halyk_agent.domain.transaction_taxonomy.category_labels import map_category_label
from halyk_agent.domain.transaction_taxonomy.membership import (
    membership_reasons,
    selector_memberships,
)
from halyk_agent.domain.transaction_taxonomy.models import (
    AdjustmentEvent,
    AdjustmentEventType,
    CalculationInput,
    ClassifiedTransaction,
)
from halyk_agent.solver.submission.models import CovenantStatus

_CAUSAL_EVENT_TYPES = frozenset(
    {
        AdjustmentEventType.CATEGORY_RECLASSIFICATION_ACCEPTED,
        AdjustmentEventType.CATEGORY_RECLASSIFICATION_REJECTED,
        AdjustmentEventType.AMOUNT_CORRECTION,
        AdjustmentEventType.PERIOD_ASSIGNMENT,
        AdjustmentEventType.PERIOD_EXCLUSION,
    }
)


def competition_verdict(result: CovenantEvaluationResult) -> CovenantStatus | None:
    """Map a fully evaluable internal result to the binary competition verdict."""

    if result.status is EvaluationStatus.NOT_ACTIVATED and result.actual is not None:
        return CovenantStatus.COMPLIANT
    if result.status is not EvaluationStatus.RESOLVED:
        return None
    if result.compliance_status is ComplianceStatus.COMPLIANT:
        return CovenantStatus.COMPLIANT
    if result.compliance_status is ComplianceStatus.BREACH:
        return CovenantStatus.BREACH
    return None


def _as_date(value: object) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _remove_fact_id(values: tuple[str, ...], fact_id: str) -> tuple[str, ...]:
    return tuple(value for value in values if value != fact_id)


def _recompute_category_fields(
    inp: CalculationInput,
    *,
    category: MetricCategory,
    description: str,
) -> CalculationInput | None:
    source_amount = inp.source_amount
    if source_amount is None:
        return None
    positive_magnitude = inp.amount_semantics.value == "POSITIVE_MAGNITUDE"
    amount_semantics, sign_rule = sign_contract_for_category(
        category,
        positive_magnitude=positive_magnitude,
    )
    metric_amount = metric_amount_from_source(
        source_amount,
        category=category,
        positive_magnitude=positive_magnitude,
    )
    return inp.model_copy(
        update={
            "category": category,
            "selector_categories": selector_memberships(category, description=description),
            "membership_reasons": membership_reasons(category, description=description),
            "amount": metric_amount,
            "metric_amount": metric_amount,
            "amount_semantics": amount_semantics,
            "sign_rule": sign_rule,
        }
    )


def _invert_event(
    inp: CalculationInput,
    event: AdjustmentEvent,
    *,
    description: str,
    classified_row: ClassifiedTransaction | None = None,
) -> CalculationInput | None:
    """Undo one authoritative treatment using the event's explicit before-state.

    The inversion is intentionally bounded to Stage 5F fields that Stage 6 consumes.
    If the before-state is insufficient, return ``None`` rather than inventing it.
    """

    if event.event_type is AdjustmentEventType.CATEGORY_RECLASSIFICATION_ACCEPTED:
        raw = event.before.get("effective_category")
        if not isinstance(raw, str):
            return None
        try:
            category = MetricCategory(raw)
        except ValueError:
            return None
        updated = _recompute_category_fields(inp, category=category, description=description)
        if updated is None:
            return None
        return updated.model_copy(
            update={
                "applied_fact_ids": _remove_fact_id(updated.applied_fact_ids, event.fact_id),
            }
        )

    if event.event_type is AdjustmentEventType.CATEGORY_RECLASSIFICATION_REJECTED:
        proposed = event.after.get("proposed_to")
        if not isinstance(proposed, str):
            return None
        proposed_category = map_category_label(proposed)
        if proposed_category is None:
            return None
        updated = _recompute_category_fields(
            inp, category=proposed_category, description=description
        )
        if updated is None:
            return None
        flags = tuple(flag for flag in updated.flags if flag != "HAS_REJECTED_RECLASSIFICATION")
        return updated.model_copy(
            update={
                "rejected_fact_ids": _remove_fact_id(updated.rejected_fact_ids, event.fact_id),
                "flags": flags,
            }
        )

    if event.event_type is AdjustmentEventType.AMOUNT_CORRECTION:
        raw_amount = event.before.get("effective_amount")
        raw_currency = event.before.get("currency")
        if raw_amount in {None, ""} and classified_row is not None:
            raw_amount = classified_row.original_amount
        if not isinstance(raw_currency, str) and classified_row is not None:
            raw_currency = classified_row.original_currency
        if raw_amount in {None, ""} or not isinstance(raw_currency, str):
            return None
        try:
            source_amount = Decimal(str(raw_amount))
        except Exception:
            return None
        positive_magnitude = inp.amount_semantics.value == "POSITIVE_MAGNITUDE"
        metric_amount = metric_amount_from_source(
            source_amount,
            category=inp.category,
            positive_magnitude=positive_magnitude,
        )
        return inp.model_copy(
            update={
                "source_amount": source_amount,
                "amount": metric_amount,
                "metric_amount": metric_amount,
                "currency": raw_currency,
                "applied_fact_ids": _remove_fact_id(inp.applied_fact_ids, event.fact_id),
            }
        )

    if event.event_type in {
        AdjustmentEventType.PERIOD_ASSIGNMENT,
        AdjustmentEventType.PERIOD_EXCLUSION,
    }:
        raw_excluded = event.before.get("period_excluded")
        if not isinstance(raw_excluded, bool):
            return None
        flags = tuple(flag for flag in inp.flags if flag != "PERIOD_EXCLUDED")
        if raw_excluded and "PERIOD_EXCLUDED" not in flags:
            flags = tuple(sorted((*flags, "PERIOD_EXCLUDED")))
        return inp.model_copy(
            update={
                "period_excluded": raw_excluded,
                "period_start": _as_date(event.before.get("period_start")),
                "period_end": _as_date(event.before.get("period_end")),
                "flags": flags,
                "applied_fact_ids": _remove_fact_id(inp.applied_fact_ids, event.fact_id),
            }
        )

    return None


def _counterfactual_context(
    context: EvaluationContext,
    *,
    transaction_id: str,
    events: tuple[AdjustmentEvent, ...],
    classified: dict[str, ClassifiedTransaction],
) -> EvaluationContext | None:
    transaction_inputs = [
        item for item in context.calculation_inputs if item.transaction_id == transaction_id
    ]
    if len(transaction_inputs) != 1:
        return None
    current = transaction_inputs[0]
    classified_row = classified.get(transaction_id)
    if classified_row is None:
        return None
    relevant = [
        event
        for event in events
        if event.transaction_id == transaction_id and event.event_type in _CAUSAL_EVENT_TYPES
    ]
    if not relevant:
        return None

    amount_was_absent = any(
        event.event_type is AdjustmentEventType.AMOUNT_CORRECTION
        and event.before.get("effective_amount") in {None, ""}
        and classified_row.original_amount is None
        for event in relevant
    )
    if amount_was_absent:
        return context.model_copy(
            update={
                "calculation_inputs": tuple(
                    item for item in context.calculation_inputs if item.input_id != current.input_id
                )
            }
        )

    priority = {
        AdjustmentEventType.CATEGORY_RECLASSIFICATION_ACCEPTED: 0,
        AdjustmentEventType.CATEGORY_RECLASSIFICATION_REJECTED: 0,
        AdjustmentEventType.AMOUNT_CORRECTION: 1,
        AdjustmentEventType.PERIOD_ASSIGNMENT: 2,
        AdjustmentEventType.PERIOD_EXCLUSION: 2,
    }
    for event in sorted(relevant, key=lambda item: (priority[item.event_type], item.event_id)):
        updated = _invert_event(
            current,
            event,
            description=classified_row.description,
            classified_row=classified_row,
        )
        if updated is None:
            return None
        current = updated

    replaced = tuple(
        current if item.input_id == transaction_inputs[0].input_id else item
        for item in context.calculation_inputs
    )
    return context.model_copy(update={"calculation_inputs": replaced})


def _scope_context_to_plan(
    context: EvaluationContext,
    plan: EvaluationPlan,
) -> EvaluationContext:
    """Bind a shared competition context to one covenant's Stage 6 universe."""

    return context.model_copy(
        update={
            "calculation_inputs": tuple(
                item for item in context.calculation_inputs if item.scenario_id == plan.scenario_id
            ),
            "selector_coverage": tuple(
                entry
                for entry in context.selector_coverage
                if entry.definition_id == plan.definition_id
                and entry.scenario_id == plan.scenario_id
            ),
            "definition_readiness": tuple(
                entry
                for entry in context.definition_readiness
                if entry.definition_id == plan.definition_id
                and entry.scenario_id == plan.scenario_id
            ),
        }
    )


def _remove_transaction_context(
    context: EvaluationContext,
    *,
    transaction_id: str,
) -> EvaluationContext | None:
    """Apply the exact fixed-context intervention ``transaction absent``.

    This is used only after authoritative treatment replay finds no unique causal
    treatment. Every Stage 6 input backed by the transaction is removed together;
    all unrelated inputs and facts remain frozen.
    """

    if not any(item.transaction_id == transaction_id for item in context.calculation_inputs):
        return None
    return context.model_copy(
        update={
            "calculation_inputs": tuple(
                item
                for item in context.calculation_inputs
                if item.transaction_id != transaction_id
            )
        }
    )


def select_causal_evidence(
    *,
    plan: EvaluationPlan,
    result: CovenantEvaluationResult,
    context: EvaluationContext,
    adjustments: tuple[AdjustmentEvent, ...],
    classified: tuple[ClassifiedTransaction, ...],
) -> str | None:
    """Return the unique transaction that causally determines the verdict.

    Evidence is resolved in two deterministic layers:

    1. Authoritative-treatment replay. Undo source-backed Stage 5F
       reclassification/inclusion/exclusion/correction events. If exactly one
       transaction flips the verdict, that treatment-causal transaction wins.
    2. Fixed-context leave-one-transaction-out replay. If no authoritative
       treatment is uniquely causal, remove each currently contributing ledger
       transaction and replay the same plan. Exactly one verdict-flipping
       transaction is publishable; zero or multiple flips remain ``None``.

    No largest/latest/threshold-crossing heuristic is used.
    """

    current_verdict = competition_verdict(result)
    if current_verdict is None:
        return None

    executor = EvaluationExecutor()
    classified_map = {row.transaction_id: row for row in classified}

    treatment_ids = sorted(
        {
            event.transaction_id
            for event in adjustments
            if event.scenario_id == plan.scenario_id
            and event.transaction_id is not None
            and event.event_type in _CAUSAL_EVENT_TYPES
        }
    )
    treatment_flips: list[str] = []
    for transaction_id in treatment_ids:
        counterfactual = _counterfactual_context(
            context,
            transaction_id=transaction_id,
            events=adjustments,
            classified=classified_map,
        )
        if counterfactual is None:
            continue
        try:
            candidate_result = executor.execute(
                plan, _scope_context_to_plan(counterfactual, plan)
            )
        except EvaluationValidationError:
            continue
        counterfactual_verdict = competition_verdict(candidate_result)
        if counterfactual_verdict is not None and counterfactual_verdict is not current_verdict:
            treatment_flips.append(transaction_id)

    if len(treatment_flips) == 1:
        return treatment_flips[0]
    if len(treatment_flips) > 1:
        return None

    candidate_ids = sorted(set(result.contributing_transaction_ids))
    absence_flips: list[str] = []
    for transaction_id in candidate_ids:
        counterfactual = _remove_transaction_context(context, transaction_id=transaction_id)
        if counterfactual is None:
            continue
        try:
            candidate_result = executor.execute(
                plan, _scope_context_to_plan(counterfactual, plan)
            )
        except EvaluationValidationError:
            continue
        counterfactual_verdict = competition_verdict(candidate_result)
        if counterfactual_verdict is not None and counterfactual_verdict is not current_verdict:
            absence_flips.append(transaction_id)

    return absence_flips[0] if len(absence_flips) == 1 else None
