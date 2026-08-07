"""Compile one covenant cell from authoritative clause evidence."""

from __future__ import annotations

from halyk_agent.domain.covenants.ast import infer_quantity_type
from halyk_agent.domain.covenants.formulas import collect_selectors, match_formula
from halyk_agent.domain.covenants.locate import find_subspan, locate_clause
from halyk_agent.domain.covenants.models import (
    CompileStatus,
    CovenantCompileFailure,
    CovenantDefinition,
    CovenantEvidenceRefs,
)
from halyk_agent.domain.covenants.parse import (
    parse_comparator,
    parse_period,
    parse_ratio_threshold,
    parse_scope,
    parse_threshold,
)
from halyk_agent.domain.covenants.quantity import CovenantTypeError, QuantityType
from halyk_agent.domain.covenants.render import render_covenant_definition
from halyk_agent.domain.evidence import EvidenceSpan
from halyk_agent.domain.ids import deterministic_id
from halyk_agent.domain.parsing import CanonicalDocument


def compile_covenant_cell(
    *,
    scenario_id: str,
    clause_id: str,
    document: CanonicalDocument,
) -> tuple[CovenantDefinition | None, CovenantCompileFailure | None, tuple[EvidenceSpan, ...]]:
    """Compile one template cell against one authoritative loan agreement."""
    spans: list[EvidenceSpan] = []
    located = locate_clause(document, clause_id=clause_id)
    if located is None:
        return (
            None,
            CovenantCompileFailure(
                failure_id=deterministic_id(
                    "covenant-failure-v1", scenario_id, clause_id, "missing-clause"
                ),
                scenario_id=scenario_id,
                clause_id=clause_id,
                status=CompileStatus.MISSING_CLAUSE,
                reason=f"clause {clause_id} not located in authoritative agreement",
                document_id=document.document_id,
            ),
            (),
        )
    if located.span is None:
        return (
            None,
            CovenantCompileFailure(
                failure_id=deterministic_id(
                    "covenant-failure-v1", scenario_id, clause_id, "missing-evidence"
                ),
                scenario_id=scenario_id,
                clause_id=clause_id,
                status=CompileStatus.MISSING_EVIDENCE,
                reason="clause located but evidence span could not be created",
                document_id=document.document_id,
            ),
            (),
        )
    spans.append(located.span)

    formula = match_formula(located.text)
    if formula is None:
        return (
            None,
            CovenantCompileFailure(
                failure_id=deterministic_id(
                    "covenant-failure-v1", scenario_id, clause_id, "unsupported"
                ),
                scenario_id=scenario_id,
                clause_id=clause_id,
                status=CompileStatus.UNSUPPORTED_FORMULA,
                reason="no supported formula family matched clause text",
                document_id=document.document_id,
                evidence_span_ids=(located.span.id,),
            ),
            tuple(spans),
        )

    comparator_parsed = parse_comparator(located.text)
    comparator = (
        formula.comparator_override
        if formula.comparator_override is not None
        else (comparator_parsed.comparator if comparator_parsed else None)
    )
    if comparator is None:
        return (
            None,
            CovenantCompileFailure(
                failure_id=deterministic_id(
                    "covenant-failure-v1", scenario_id, clause_id, "ambiguous-op"
                ),
                scenario_id=scenario_id,
                clause_id=clause_id,
                status=CompileStatus.AMBIGUOUS_OPERATOR,
                reason="comparator could not be determined",
                document_id=document.document_id,
                evidence_span_ids=(located.span.id,),
            ),
            tuple(spans),
        )

    threshold_parsed = parse_threshold(located.text)
    threshold = formula.threshold_override or (
        threshold_parsed.quantity if threshold_parsed else None
    )
    # Springing: primary threshold is ratio; money is activation.
    if formula.family_id == "SPRINGING_DRAWDOWN_LEVERAGE":
        ratio_thr = parse_ratio_threshold(located.text)
        if ratio_thr is not None:
            threshold = ratio_thr.quantity
            threshold_parsed = ratio_thr

    if threshold is None:
        return (
            None,
            CovenantCompileFailure(
                failure_id=deterministic_id(
                    "covenant-failure-v1", scenario_id, clause_id, "ambiguous-thr"
                ),
                scenario_id=scenario_id,
                clause_id=clause_id,
                status=CompileStatus.AMBIGUOUS_THRESHOLD,
                reason="threshold quantity could not be determined",
                document_id=document.document_id,
                evidence_span_ids=(located.span.id,),
            ),
            tuple(spans),
        )

    period = parse_period(located.text)
    if period is None:
        return (
            None,
            CovenantCompileFailure(
                failure_id=deterministic_id(
                    "covenant-failure-v1", scenario_id, clause_id, "period"
                ),
                scenario_id=scenario_id,
                clause_id=clause_id,
                status=CompileStatus.UNRESOLVED_PERIOD,
                reason="measurement period could not be determined",
                document_id=document.document_id,
                evidence_span_ids=(located.span.id,),
            ),
            tuple(spans),
        )

    scope = parse_scope(located.text)

    try:
        metric_type = infer_quantity_type(formula.metric)
    except CovenantTypeError as exc:
        return (
            None,
            CovenantCompileFailure(
                failure_id=deterministic_id("covenant-failure-v1", scenario_id, clause_id, "type"),
                scenario_id=scenario_id,
                clause_id=clause_id,
                status=CompileStatus.TYPE_ERROR,
                reason=exc.message,
                document_id=document.document_id,
                evidence_span_ids=(located.span.id,),
            ),
            tuple(spans),
        )

    # Threshold type must match metric output (PERCENT may compare as RATIO).
    thr_type = threshold.quantity_type
    if thr_type is QuantityType.PERCENT:
        thr_type = QuantityType.RATIO
        threshold = threshold.as_ratio()
    if metric_type is not thr_type:
        return (
            None,
            CovenantCompileFailure(
                failure_id=deterministic_id(
                    "covenant-failure-v1", scenario_id, clause_id, "type-mismatch"
                ),
                scenario_id=scenario_id,
                clause_id=clause_id,
                status=CompileStatus.TYPE_ERROR,
                reason=(
                    f"metric type {metric_type.value} incompatible with "
                    f"threshold type {threshold.quantity_type.value}"
                ),
                document_id=document.document_id,
                evidence_span_ids=(located.span.id,),
            ),
            tuple(spans),
        )

    def _span_for(matched: str | None) -> tuple[str, ...]:
        if not matched:
            return ()
        span = find_subspan(
            document,
            page_number=located.page_number,
            clause_start=located.char_start,
            clause_text=located.text,
            needle=matched,
        )
        if span is None:
            return ()
        spans.append(span)
        return (span.id,)

    comparator_spans = _span_for(comparator_parsed.matched_text if comparator_parsed else None)
    threshold_spans = _span_for(threshold_parsed.matched_text if threshold_parsed else None)
    period_needle = None
    if period.start_date and period.end_date:
        period_needle = f"{period.start_date.isoformat()}"
    elif period.as_of_date:
        period_needle = period.as_of_date.isoformat()
    period_spans = _span_for(period_needle)
    period = period.model_copy(update={"evidence_span_ids": period_spans})
    scope = scope.model_copy(update={"evidence_span_ids": ()})

    evidence = CovenantEvidenceRefs(
        clause_span_ids=(located.span.id,),
        formula_span_ids=(located.span.id,),
        comparator_span_ids=comparator_spans,
        threshold_span_ids=threshold_spans,
        period_span_ids=period_spans,
        scope_span_ids=(),
    )

    definition = CovenantDefinition(
        definition_id=deterministic_id(
            "covenant-definition-v1",
            scenario_id,
            clause_id,
            document.document_id,
            formula.family_id,
        ),
        scenario_id=scenario_id,
        clause_id=clause_id,
        document_id=document.document_id,
        document_version_id=document.document_version_id,
        source_file=document.source_file,
        source_sha256=document.source_sha256 or ("0" * 64),
        family_id=formula.family_id,
        metric=formula.metric,
        metric_quantity_type=metric_type,
        comparator=comparator,
        threshold=threshold,
        period=period,
        scope=scope,
        selectors=collect_selectors(formula.metric),
        activation_condition=formula.activation_condition,
        evidence=evidence,
        rendered="pending",
    )
    rendered = render_covenant_definition(definition)
    definition = definition.model_copy(update={"rendered": rendered})
    return definition, None, tuple(spans)
