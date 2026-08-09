"""Compile one covenant cell from authoritative clause evidence."""

from __future__ import annotations

from halyk_agent.config import Settings, get_settings
from halyk_agent.domain.covenants.ast import (
    BoolExpr,
    Compare,
    Constant,
    infer_quantity_type,
)
from halyk_agent.domain.covenants.formulas import collect_selectors, match_formula
from halyk_agent.domain.covenants.locate import (
    LocatedClause,
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
    CovenantPlan,
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
from halyk_agent.domain.covenants.plans import (
    derive_primary_comparison,
    plan_selectors,
    simple_plan,
)
from halyk_agent.domain.covenants.quantity import CovenantTypeError, QuantityType
from halyk_agent.domain.covenants.render import render_covenant_definition
from halyk_agent.domain.covenants.semantic_formula import propose_formula
from halyk_agent.domain.covenants.semantic_plan import propose_plan
from halyk_agent.domain.covenants.structure import match_structure
from halyk_agent.domain.evidence import EvidenceSpan
from halyk_agent.domain.ids import deterministic_id
from halyk_agent.domain.models_gateway.semantic_json import SemanticJsonGateway
from halyk_agent.domain.parsing import CanonicalDocument


def _definition_from_plan(
    *,
    scenario_id: str,
    clause_id: str,
    document: CanonicalDocument,
    located: LocatedClause,
    spans: list[EvidenceSpan],
    plan: CovenantPlan,
    family_id: str,
    parse_method: str,
) -> tuple[CovenantDefinition | None, CovenantCompileFailure | None, tuple[EvidenceSpan, ...]]:
    """Build a CovenantDefinition whose authority is the typed plan."""
    clause_span = located.span
    assert clause_span is not None

    def _fail(
        status: CompileStatus, reason: str
    ) -> tuple[None, CovenantCompileFailure, tuple[EvidenceSpan, ...]]:
        return (
            None,
            CovenantCompileFailure(
                failure_id=deterministic_id(
                    "covenant-failure-v1", scenario_id, clause_id, status.value
                ),
                scenario_id=scenario_id,
                clause_id=clause_id,
                status=status,
                reason=reason,
                document_id=document.document_id,
                evidence_span_ids=(clause_span.id,),
            ),
            tuple(spans),
        )

    period = parse_period(located.text)
    if period is None:
        return _fail(CompileStatus.UNRESOLVED_PERIOD, "measurement period could not be determined")

    try:
        metric_type = infer_quantity_type(plan.reported_actual)
    except CovenantTypeError as exc:
        return _fail(CompileStatus.TYPE_ERROR, exc.message)

    selectors = plan_selectors(plan)
    scope = resolve_scope(located.text, selectors=selectors)
    primary = derive_primary_comparison(plan)

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

    modifier_spans: list[str] = []
    enriched_mods: list[CovenantModifier] = []
    for match in extract_modifier_matches(located.text):
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
                threshold=match.threshold,
                applies_to_category=match.applies_to_category,
            )
        )

    evidence = CovenantEvidenceRefs(
        clause_span_ids=(clause_span.id,),
        formula_span_ids=tuple(span.id for span in formula_spans),
        scope_span_ids=_clause_span(scope.matched_text),
        modifier_span_ids=tuple(dict.fromkeys(modifier_spans)),
    )

    definition = CovenantDefinition(
        definition_id=deterministic_id(
            "covenant-definition-v1", scenario_id, clause_id, document.document_id, family_id
        ),
        scenario_id=scenario_id,
        clause_id=clause_id,
        document_id=document.document_id,
        document_version_id=document.document_version_id,
        source_file=document.source_file,
        source_sha256=document.source_sha256 or ("0" * 64),
        family_id=family_id,
        metric=plan.reported_actual,
        metric_quantity_type=metric_type,
        comparator=primary[0] if primary else None,
        threshold=primary[1] if primary else None,
        period=period,
        scope=scope,
        selectors=selectors,
        modifiers=tuple(enriched_mods),
        plan=plan,
        parse_method=parse_method,
        evidence=evidence,
        rendered="pending",
    )
    definition = definition.model_copy(update={"rendered": render_covenant_definition(definition)})
    return definition, None, tuple(spans)


def compile_covenant_cell(
    *,
    scenario_id: str,
    clause_id: str,
    document: CanonicalDocument,
    settings: Settings | None = None,
    semantic_gateway: SemanticJsonGateway | None = None,
) -> tuple[CovenantDefinition | None, CovenantCompileFailure | None, tuple[EvidenceSpan, ...]]:
    """Compile one template cell against one authoritative loan agreement."""
    spans: list[EvidenceSpan] = []
    located = locate_clause(document, clause_id=clause_id)
    if located is None:
        # Without clause text there is nothing to interpret, deterministically
        # or semantically.
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
    semantic_plan_attempted = False

    def _semantic_plan_or(
        failure: CovenantCompileFailure,
    ) -> tuple[CovenantDefinition | None, CovenantCompileFailure | None, tuple[EvidenceSpan, ...]]:
        """Try the bounded planner before giving up on a cell.

        A family can match the wording yet still fail on threshold or type,
        typically because the clause carries a basket, a proviso or a second
        amount the legacy triple cannot hold. Those clauses are exactly the ones
        the typed plan can express, so a legacy failure is a reason to escalate
        rather than to stop.
        """
        nonlocal semantic_plan_attempted
        resolved = settings or get_settings()
        if semantic_plan_attempted or not resolved.semantic_fallback_enabled:
            return None, failure, tuple(spans)
        semantic_plan_attempted = True
        proposal = propose_plan(
            located.text,
            scenario_id=scenario_id,
            clause_id=clause_id,
            settings=resolved,
            gateway=semantic_gateway,
        )
        if proposal.plan is None:
            return None, failure, tuple(spans)
        return _definition_from_plan(
            scenario_id=scenario_id,
            clause_id=clause_id,
            document=document,
            located=located,
            spans=spans,
            plan=proposal.plan,
            family_id="DEEPSEEK_TYPED_PLAN_V2",
            parse_method="deepseek_plan",
        )

    # A structural plan is preferred over the legacy family triple: it can carry
    # springing activation, compound breach logic, expression-valued thresholds,
    # period extrema and accounting scope, none of which the triple can express.
    structural = match_structure(located.text)
    if structural is not None and (formula is None or structural.overrides_family):
        return _definition_from_plan(
            scenario_id=scenario_id,
            clause_id=clause_id,
            document=document,
            located=located,
            spans=spans,
            plan=structural.plan,
            family_id=structural.family_id,
            parse_method="deterministic_structure",
        )

    if formula is None:
        resolved_settings = settings or get_settings()
        if resolved_settings.semantic_fallback_enabled:
            semantic_plan_attempted = True
            semantic_plan = propose_plan(
                located.text,
                scenario_id=scenario_id,
                clause_id=clause_id,
                settings=resolved_settings,
                gateway=semantic_gateway,
            )
            if semantic_plan.plan is not None:
                return _definition_from_plan(
                    scenario_id=scenario_id,
                    clause_id=clause_id,
                    document=document,
                    located=located,
                    spans=spans,
                    plan=semantic_plan.plan,
                    family_id="DEEPSEEK_TYPED_PLAN_V2",
                    parse_method="deepseek_plan",
                )
            # Legacy metric-only recovery remains available for clauses whose
            # only gap was the formula shape.
            semantic = propose_formula(
                located.text,
                scenario_id=scenario_id,
                clause_id=clause_id,
                settings=resolved_settings,
                gateway=semantic_gateway,
            )
            formula = semantic.formula
    if formula is None:
        return _semantic_plan_or(
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
            )
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
        # A malformed number is a data-integrity failure, not a semantic gap.
        # Asking a model to read it would invite it to invent a clean amount, so
        # this one stays fail-closed and is never escalated.
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
        return _semantic_plan_or(
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
            )
        )
    elif threshold_result.status != "ok" or threshold_result.quantity is None:
        return _semantic_plan_or(
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
            )
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
        return _semantic_plan_or(
            CovenantCompileFailure(
                failure_id=deterministic_id("covenant-failure-v1", scenario_id, clause_id, "type"),
                scenario_id=scenario_id,
                clause_id=clause_id,
                status=CompileStatus.TYPE_ERROR,
                reason=exc.message,
                document_id=document.document_id,
                evidence_span_ids=(located.span.id,),
            )
        )

    thr_type = threshold.quantity_type
    if thr_type is QuantityType.PERCENT:
        thr_type = QuantityType.RATIO
        threshold = threshold.as_ratio()
    if metric_type is not thr_type:
        return _semantic_plan_or(
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
            )
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
                threshold=match.threshold,
                applies_to_category=match.applies_to_category,
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

    # Every definition carries typed plan semantics, including the legacy
    # families: a plain covenant is the degenerate plan (activation ALWAYS,
    # breach = reported actual vs constant).
    legacy_activation: BoolExpr | None = None
    if activation is not None:
        legacy_activation = Compare(
            left=activation.metric,
            comparator=activation.comparator,
            right=Constant(quantity=activation.threshold),
        )
    try:
        plan = simple_plan(
            metric=formula.metric,
            compliance_comparator=comparator,
            threshold=threshold,
            activation=legacy_activation,
        )
    except CovenantTypeError:
        plan = None

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
        plan=plan,
        parse_method=(
            "deepseek_formula"
            if formula.family_id == "DEEPSEEK_TYPED_AST_V1"
            else "deterministic_family"
        ),
        evidence=evidence,
        rendered="pending",
    )
    rendered = render_covenant_definition(definition)
    definition = definition.model_copy(update={"rendered": rendered})
    return definition, None, tuple(spans)
