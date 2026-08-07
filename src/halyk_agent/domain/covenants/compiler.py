"""Compile one covenant cell from authoritative clause evidence."""

from __future__ import annotations

from halyk_agent.domain.covenants.ast import infer_quantity_type
from halyk_agent.domain.covenants.formulas import collect_selectors, match_formula
from halyk_agent.domain.covenants.locate import (
    find_in_clause,
    formula_region_spans,
    locate_clause,
)
from halyk_agent.domain.covenants.models import (
    ActivationCondition,
    CompileStatus,
    CovenantCompileFailure,
    CovenantDefinition,
    CovenantEvidenceRefs,
    CovenantModifier,
    ScopeProvenance,
)
from halyk_agent.domain.covenants.modifiers import extract_modifier_matches
from halyk_agent.domain.covenants.parse import (
    ThresholdRole,
    collect_threshold_candidates,
    parse_comparator,
    parse_period,
    parse_ratio_threshold,
    parse_threshold,
    resolve_scope,
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

    prefer_ratio = formula.family_id == "SPRINGING_DRAWDOWN_LEVERAGE" or (
        formula.threshold_override is None
        and any(
            tok in formula.family_id
            for tok in ("RATIO", "MARGIN", "COVER", "INTENSITY", "LEVERAGE")
        )
    )
    if formula.family_id == "SPRINGING_DRAWDOWN_LEVERAGE":
        threshold_result = parse_ratio_threshold(located.text)
    elif prefer_ratio and formula.threshold_override is None:
        threshold_result = parse_ratio_threshold(located.text)
        if threshold_result.status != "ok":
            threshold_result = parse_threshold(located.text)
    else:
        threshold_result = parse_threshold(located.text)

    if formula.threshold_override is not None:
        threshold = formula.threshold_override
        threshold_matched = None
    elif threshold_result.status == "malformed":
        return (
            None,
            CovenantCompileFailure(
                failure_id=deterministic_id(
                    "covenant-failure-v1", scenario_id, clause_id, "malformed-thr"
                ),
                scenario_id=scenario_id,
                clause_id=clause_id,
                status=CompileStatus.MALFORMED_THRESHOLD,
                reason=threshold_result.reason or "malformed threshold",
                document_id=document.document_id,
                evidence_span_ids=(located.span.id,),
            ),
            tuple(spans),
        )
    elif threshold_result.status == "ambiguous":
        return (
            None,
            CovenantCompileFailure(
                failure_id=deterministic_id(
                    "covenant-failure-v1", scenario_id, clause_id, "ambiguous-thr"
                ),
                scenario_id=scenario_id,
                clause_id=clause_id,
                status=CompileStatus.AMBIGUOUS_THRESHOLD,
                reason=threshold_result.reason or "ambiguous threshold",
                document_id=document.document_id,
                evidence_span_ids=(located.span.id,),
            ),
            tuple(spans),
        )
    elif threshold_result.status != "ok" or threshold_result.quantity is None:
        return (
            None,
            CovenantCompileFailure(
                failure_id=deterministic_id(
                    "covenant-failure-v1", scenario_id, clause_id, "missing-thr"
                ),
                scenario_id=scenario_id,
                clause_id=clause_id,
                status=CompileStatus.AMBIGUOUS_THRESHOLD,
                reason=threshold_result.reason or "threshold quantity could not be determined",
                document_id=document.document_id,
                evidence_span_ids=(located.span.id,),
            ),
            tuple(spans),
        )
    else:
        threshold = threshold_result.quantity
        threshold_matched = threshold_result.matched_text

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

    selectors = collect_selectors(formula.metric)
    scope = resolve_scope(located.text, selectors=selectors)

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

    def _clause_span(matched: str | None) -> tuple[str, ...]:
        if not matched:
            return ()
        span = find_in_clause(document, located, needle=matched)
        if span is None:
            return ()
        spans.append(span)
        return (span.id,)

    formula_spans = formula_region_spans(document, located)
    spans.extend(formula_spans)
    formula_span_ids = tuple(span.id for span in formula_spans)

    comparator_spans = _clause_span(comparator_parsed.matched_text if comparator_parsed else None)
    threshold_spans = _clause_span(threshold_matched)
    period_needle = None
    if period.as_of_date and period.flow_start_date:
        period_needle = period.as_of_date.isoformat()
    elif period.start_date and period.end_date:
        period_needle = f"{period.start_date.isoformat()}"
    elif period.as_of_date:
        period_needle = period.as_of_date.isoformat()
    period_spans = _clause_span(period_needle)
    # Also capture flow dates for mixed periods.
    if period.flow_start_date is not None:
        period_spans = tuple(
            dict.fromkeys(
                (
                    *period_spans,
                    *_clause_span(period.flow_start_date.isoformat()),
                    *_clause_span(
                        period.flow_end_date.isoformat() if period.flow_end_date else None
                    ),
                )
            )
        )
    period = period.model_copy(update={"evidence_span_ids": period_spans})

    scope_spans: tuple[str, ...] = ()
    if scope.matched_text:
        scope_spans = _clause_span(scope.matched_text)
    elif scope.provenance is ScopeProvenance.DEFAULT_BORROWER_BY_RULE:
        scope_spans = ()
    scope = scope.model_copy(update={"evidence_span_ids": scope_spans})

    # Activation evidence for springing covenants.
    activation = formula.activation_condition
    activation_spans: tuple[str, ...] = ()
    if activation is not None:
        act_candidates = [
            c
            for c in collect_threshold_candidates(located.text)
            if c.role is ThresholdRole.ACTIVATION and c.quantity.quantity_type is QuantityType.MONEY
        ]
        if act_candidates:
            act = act_candidates[0]
            activation = ActivationCondition(
                metric=activation.metric,
                comparator=activation.comparator,
                threshold=act.quantity,
                evidence_span_ids=(),
            )
            activation_spans = _clause_span(act.matched_text)
            # Also cite activation predicate phrase when present.
            for phrase in ("только при условии", "при условии"):
                extra = _clause_span(phrase)
                if extra:
                    activation_spans = tuple(dict.fromkeys((*activation_spans, *extra)))
                    break
            activation = activation.model_copy(update={"evidence_span_ids": activation_spans})

    modifier_spans: list[str] = []
    enriched_mods: list[CovenantModifier] = []
    for match in extract_modifier_matches(located.text):
        # Evidence comes from the same matcher event (exact quotes), not a second grammar.
        ids: list[str] = []
        for quote in match.quotes:
            ids.extend(_clause_span(quote))
        span_ids = tuple(dict.fromkeys(ids))
        modifier_spans.extend(span_ids)
        enriched_mods.append(
            CovenantModifier(
                kind=match.kind,
                detail=match.detail,
                evidence_span_ids=span_ids,
            )
        )

    evidence = CovenantEvidenceRefs(
        clause_span_ids=(located.span.id,),
        formula_span_ids=formula_span_ids,
        comparator_span_ids=comparator_spans,
        threshold_span_ids=threshold_spans,
        period_span_ids=period_spans,
        scope_span_ids=scope_spans,
        activation_span_ids=activation_spans,
        modifier_span_ids=tuple(dict.fromkeys(modifier_spans)),
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
        selectors=selectors,
        modifiers=tuple(enriched_mods),
        activation_condition=activation,
        evidence=evidence,
        rendered="pending",
    )
    rendered = render_covenant_definition(definition)
    definition = definition.model_copy(update={"rendered": rendered})
    return definition, None, tuple(spans)
