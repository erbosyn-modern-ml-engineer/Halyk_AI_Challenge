"""Deterministic CovenantDefinition -> EvaluationPlan compiler."""

from __future__ import annotations

from collections.abc import Iterable

from halyk_agent.domain.covenants.ast import (
    Add,
    Constant,
    Count,
    Divide,
    Expr,
    Max,
    MetricCategory,
    Min,
    Multiply,
    PeriodAggregate,
    PeriodGrouping,
    Subtract,
    Sum,
    TransactionSet,
    infer_quantity_type,
)
from halyk_agent.domain.covenants.models import (
    CovenantDefinition,
    CovenantModifier,
    CovenantModifierKind,
)
from halyk_agent.domain.covenants.quantity import QuantityType
from halyk_agent.domain.ids import deterministic_id

from .models import EvaluationNode, EvaluationNodeKind, EvaluationPlan


class EvaluationPlanningError(ValueError):
    def __init__(self, message: str, *, code: str = "EVALUATION_PLANNING_ERROR") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


_UPSTREAM_CONSUMED_MODIFIERS = {
    CovenantModifierKind.AUDITOR_RECLASSIFICATION_INCLUDE,
    CovenantModifierKind.AUDITOR_RECLASSIFICATION_EXCLUDE,
    CovenantModifierKind.REJECTED_RECLASSIFICATION_EXCLUDE,
    CovenantModifierKind.BOTH_NUMERATOR_AND_DENOMINATOR_RECLASS,
}


def _all_selector_categories(definition: CovenantDefinition) -> set[MetricCategory]:
    return {selector.category for selector in definition.selectors}


def _materiality_modifier(definition: CovenantDefinition) -> CovenantModifier | None:
    matches = [
        modifier
        for modifier in definition.modifiers
        if modifier.kind is CovenantModifierKind.MATERIALITY_FLOOR
    ]
    if not matches:
        return None
    if len(matches) != 1:
        raise EvaluationPlanningError(
            "multiple MATERIALITY_FLOOR modifiers survived covenant compilation",
            code="MULTIPLE_MATERIALITY_MODIFIERS",
        )
    modifier = matches[0]
    if modifier.threshold is None:
        raise EvaluationPlanningError(
            "MATERIALITY_FLOOR is missing a typed threshold",
            code="MATERIALITY_THRESHOLD_MISSING",
        )
    if modifier.threshold.quantity_type is not QuantityType.MONEY:
        raise EvaluationPlanningError(
            "MATERIALITY_FLOOR threshold must be MONEY",
            code="MATERIALITY_THRESHOLD_TYPE",
        )
    return modifier


def _materiality_target(
    definition: CovenantDefinition,
    modifier: CovenantModifier | None,
) -> MetricCategory | None:
    if modifier is None:
        return None
    if modifier.applies_to_category is not None:
        return modifier.applies_to_category
    categories = _all_selector_categories(definition)
    if len(categories) == 1:
        return next(iter(categories))
    raise EvaluationPlanningError(
        "materiality modifier has no target category and the definition has "
        "multiple selector categories",
        code="MATERIALITY_TARGET_AMBIGUOUS",
    )


def _definition_evidence_ids(definition: CovenantDefinition) -> tuple[str, ...]:
    refs = definition.evidence
    items: list[str] = []
    for group in (
        refs.clause_span_ids,
        refs.formula_span_ids,
        refs.comparator_span_ids,
        refs.threshold_span_ids,
        refs.period_span_ids,
        refs.scope_span_ids,
        refs.activation_span_ids,
        refs.modifier_span_ids,
    ):
        items.extend(group)
    return tuple(dict.fromkeys(items))


def plan_definition(definition: CovenantDefinition) -> EvaluationPlan:
    """Translate one typed covenant AST into a small immutable execution DAG."""

    for modifier in definition.modifiers:
        if (
            modifier.kind not in _UPSTREAM_CONSUMED_MODIFIERS
            and modifier.kind is not CovenantModifierKind.MATERIALITY_FLOOR
        ):
            raise EvaluationPlanningError(
                f"unsupported covenant modifier: {modifier.kind.value}",
                code="UNSUPPORTED_MODIFIER",
            )

    # DSL v2 plans whose semantics do not reduce to "actual <cmp> constant"
    # (compound breach logic, expression-valued thresholds, sub-period extrema)
    # need the plan-aware executor. Fail closed with a specific code rather than
    # silently evaluating a lossy approximation of the covenant.
    if definition.comparator is None or definition.threshold is None:
        raise EvaluationPlanningError(
            "covenant semantics require the plan-aware evaluator "
            "(compound logic or expression-valued threshold)",
            code="PLAN_REQUIRES_V2_EVALUATOR",
        )

    materiality = _materiality_modifier(definition)
    materiality_target = _materiality_target(definition, materiality)

    nodes: dict[str, EvaluationNode] = {}
    expression_cache: dict[str, str] = {}

    def add_node(node: EvaluationNode) -> str:
        existing = nodes.get(node.node_id)
        if existing is not None and existing != node:
            raise EvaluationPlanningError(
                f"deterministic node id collision: {node.node_id}",
                code="NODE_ID_COLLISION",
            )
        nodes[node.node_id] = node
        return node.node_id

    def node_id(kind: EvaluationNodeKind, *parts: object) -> str:
        return deterministic_id(
            "halyk-evaluation-node-v1",
            definition.definition_id,
            kind.value,
            *(str(part) for part in parts),
        )

    def plan_expr(expr: Expr) -> str:
        cache_key = expr.model_dump_json()
        cached = expression_cache.get(cache_key)
        if cached is not None:
            return cached

        if isinstance(expr, TransactionSet):
            selector_json = expr.selector.model_dump_json()
            select_id = node_id(EvaluationNodeKind.SELECT, selector_json)
            current = add_node(
                EvaluationNode(
                    node_id=select_id,
                    kind=EvaluationNodeKind.SELECT,
                    selector=expr.selector,
                )
            )
            if (
                materiality is not None
                and materiality_target is not None
                and expr.selector.category is materiality_target
            ):
                assert materiality.threshold is not None
                filter_id = node_id(
                    EvaluationNodeKind.MATERIALITY_FILTER,
                    current,
                    materiality.threshold.model_dump_json(),
                    materiality_target.value,
                )
                current = add_node(
                    EvaluationNode(
                        node_id=filter_id,
                        kind=EvaluationNodeKind.MATERIALITY_FILTER,
                        dependencies=(current,),
                        materiality_threshold=materiality.threshold,
                        materiality_category=materiality_target,
                    )
                )
            expression_cache[cache_key] = current
            return current

        if isinstance(expr, Constant):
            qtype = expr.quantity.quantity_type
            current = add_node(
                EvaluationNode(
                    node_id=node_id(
                        EvaluationNodeKind.CONSTANT,
                        expr.quantity.model_dump_json(),
                    ),
                    kind=EvaluationNodeKind.CONSTANT,
                    quantity_type=qtype,
                    constant=expr.quantity,
                )
            )
            expression_cache[cache_key] = current
            return current

        if isinstance(expr, Sum):
            dependency = plan_expr(expr.of)
            current = add_node(
                EvaluationNode(
                    node_id=node_id(EvaluationNodeKind.SUM, dependency),
                    kind=EvaluationNodeKind.SUM,
                    dependencies=(dependency,),
                    quantity_type=QuantityType.MONEY,
                )
            )
        elif isinstance(expr, Count):
            dependency = plan_expr(expr.of)
            current = add_node(
                EvaluationNode(
                    node_id=node_id(EvaluationNodeKind.COUNT, dependency),
                    kind=EvaluationNodeKind.COUNT,
                    dependencies=(dependency,),
                    quantity_type=QuantityType.COUNT,
                )
            )
        elif isinstance(expr, Min):
            dependencies = tuple(plan_expr(arg) for arg in expr.args)
            current = add_node(
                EvaluationNode(
                    node_id=node_id(EvaluationNodeKind.MIN, *dependencies),
                    kind=EvaluationNodeKind.MIN,
                    dependencies=dependencies,
                    quantity_type=infer_quantity_type(expr),
                )
            )
        elif isinstance(expr, Max):
            dependencies = tuple(plan_expr(arg) for arg in expr.args)
            current = add_node(
                EvaluationNode(
                    node_id=node_id(EvaluationNodeKind.MAX, *dependencies),
                    kind=EvaluationNodeKind.MAX,
                    dependencies=dependencies,
                    quantity_type=infer_quantity_type(expr),
                )
            )
        elif isinstance(expr, Add):
            left = plan_expr(expr.left)
            right = plan_expr(expr.right)
            current = add_node(
                EvaluationNode(
                    node_id=node_id(EvaluationNodeKind.ADD, left, right),
                    kind=EvaluationNodeKind.ADD,
                    dependencies=(left, right),
                    quantity_type=infer_quantity_type(expr),
                )
            )
        elif isinstance(expr, Subtract):
            left = plan_expr(expr.left)
            right = plan_expr(expr.right)
            current = add_node(
                EvaluationNode(
                    node_id=node_id(EvaluationNodeKind.SUBTRACT, left, right),
                    kind=EvaluationNodeKind.SUBTRACT,
                    dependencies=(left, right),
                    quantity_type=infer_quantity_type(expr),
                )
            )
        elif isinstance(expr, Multiply):
            left = plan_expr(expr.left)
            right = plan_expr(expr.right)
            current = add_node(
                EvaluationNode(
                    node_id=node_id(EvaluationNodeKind.MULTIPLY, left, right),
                    kind=EvaluationNodeKind.MULTIPLY,
                    dependencies=(left, right),
                    quantity_type=infer_quantity_type(expr),
                )
            )
        elif isinstance(expr, Divide):
            numerator = plan_expr(expr.numerator)
            denominator = plan_expr(expr.denominator)
            current = add_node(
                EvaluationNode(
                    node_id=node_id(EvaluationNodeKind.DIVIDE, numerator, denominator),
                    kind=EvaluationNodeKind.DIVIDE,
                    dependencies=(numerator, denominator),
                    quantity_type=infer_quantity_type(expr),
                )
            )
        elif isinstance(expr, PeriodAggregate):
            if expr.grouping is PeriodGrouping.FULL_PERIOD:
                # One bucket covering the whole measurement period: reducing it
                # is the operand itself, so this is exact, not an approximation.
                current = plan_expr(expr.of)
                expression_cache[cache_key] = current
                return current
            # Genuine sub-period reduction needs per-transaction period
            # assignment from Stage 5F. Name the blocker explicitly instead of
            # collapsing the covenant into an annual total, which would be a
            # different covenant.
            raise EvaluationPlanningError(
                f"period aggregation ({expr.reducer.value} over {expr.grouping.value}) "
                "requires per-period calculation inputs",
                code="PERIOD_INPUTS_REQUIRED",
            )
        else:
            raise EvaluationPlanningError(
                f"unsupported AST node: {type(expr).__name__}",
                code="UNSUPPORTED_AST_NODE",
            )

        expression_cache[cache_key] = current
        return current

    root = plan_expr(definition.metric)

    activation_root: str | None = None
    activation_comparator = None
    activation_threshold = None
    if definition.activation_condition is not None:
        activation_root = plan_expr(definition.activation_condition.metric)
        activation_comparator = definition.activation_condition.comparator
        activation_threshold = definition.activation_condition.threshold

    if materiality is not None and materiality_target is not None:
        target_filters = [
            node
            for node in nodes.values()
            if node.kind is EvaluationNodeKind.MATERIALITY_FILTER
            and node.materiality_category is materiality_target
        ]
        if not target_filters:
            raise EvaluationPlanningError(
                f"materiality target {materiality_target.value} is absent from the metric AST",
                code="MATERIALITY_TARGET_NOT_IN_METRIC",
            )

    plan_id = deterministic_id(
        "halyk-evaluation-plan-v1",
        definition.definition_id,
        root,
        activation_root,
        definition.comparator.value,
        definition.threshold.model_dump_json(),
    )
    return EvaluationPlan(
        plan_id=plan_id,
        definition_id=definition.definition_id,
        scenario_id=definition.scenario_id,
        clause_id=definition.clause_id,
        family_id=definition.family_id,
        source_file=definition.source_file,
        source_sha256=definition.source_sha256,
        nodes=tuple(sorted(nodes.values(), key=lambda item: item.node_id)),
        root_node_id=root,
        metric_quantity_type=definition.metric_quantity_type,
        comparator=definition.comparator,
        threshold=definition.threshold,
        period=definition.period,
        scope=definition.scope,
        modifiers=definition.modifiers,
        activation_root_node_id=activation_root,
        activation_comparator=activation_comparator,
        activation_threshold=activation_threshold,
        definition_evidence_span_ids=_definition_evidence_ids(definition),
    )


def plan_definitions_partial(
    definitions: Iterable[CovenantDefinition],
) -> tuple[tuple[EvaluationPlan, ...], tuple[tuple[CovenantDefinition, EvaluationPlanningError], ...]]:
    """Plan what can be planned; return per-cell blockers instead of aborting.

    One covenant the evaluator cannot yet execute must not suppress every other
    covenant's result. Each unplannable definition is reported with its own
    fail-closed reason and simply produces no plan.
    """
    plans: list[EvaluationPlan] = []
    blocked: list[tuple[CovenantDefinition, EvaluationPlanningError]] = []
    for definition in sorted(
        definitions, key=lambda item: (item.scenario_id, item.clause_id, item.definition_id)
    ):
        try:
            plans.append(plan_definition(definition))
        except EvaluationPlanningError as exc:
            blocked.append((definition, exc))
    return tuple(plans), tuple(blocked)


def plan_definitions(definitions: Iterable[CovenantDefinition]) -> tuple[EvaluationPlan, ...]:
    """Plan definitions in deterministic identity order."""

    return tuple(
        plan_definition(definition)
        for definition in sorted(
            definitions,
            key=lambda item: (item.scenario_id, item.clause_id, item.definition_id),
        )
    )
