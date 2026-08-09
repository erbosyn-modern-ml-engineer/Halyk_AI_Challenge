"""Typed Stage 5F context validation for covenant evaluation."""

from __future__ import annotations

from halyk_agent.domain.covenants.quantity import QuantityType
from halyk_agent.domain.transaction_taxonomy.models import (
    AMOUNT_CONTRACT_VERSION,
    CalculationInput,
    SelectorCoverageEntry,
    SelectorReadinessStatus,
)

from .models import (
    EvaluationContext,
    EvaluationIssue,
    EvaluationIssueSeverity,
    EvaluationNodeKind,
    EvaluationPlan,
)
from .selection import input_matches_selector_strict, input_period_membership
from .structure_validation import EvaluationValidationError, PlanStructureValidator


def _coverage_key(entry: SelectorCoverageEntry) -> tuple[object, ...]:
    return (
        entry.definition_id,
        entry.scenario_id,
        entry.category,
        entry.related_party_only,
        entry.group_level,
        entry.include_flags,
        entry.exclude_flags,
    )


def _selector_key(
    plan: EvaluationPlan,
    node_id: str,
) -> tuple[object, ...]:
    node = next(node for node in plan.nodes if node.node_id == node_id)
    assert node.selector is not None
    selector = node.selector
    return (
        plan.definition_id,
        plan.scenario_id,
        selector.category,
        selector.related_party_only,
        selector.group_level,
        selector.include_flags,
        selector.exclude_flags,
    )


def _issue(
    code: str,
    message: str,
    *,
    node_id: str | None = None,
    input_ids: tuple[str, ...] = (),
) -> EvaluationIssue:
    return EvaluationIssue(
        code=code,
        severity=EvaluationIssueSeverity.UNRESOLVED,
        message=message,
        node_id=node_id,
        input_ids=input_ids,
    )


class ContextValidator:
    """Validate artifact integrity and per-plan execution context.

    Structural corruption raises ``EvaluationValidationError`` so the app layer
    publishes nothing. Expected business incompleteness (e.g. a selector marked
    UNRESOLVED or mixed currency without a trusted conversion) is returned as
    typed issues so that the individual covenant fails closed.
    """

    def validate_global(
        self,
        plans: tuple[EvaluationPlan, ...],
        context: EvaluationContext,
    ) -> None:
        if context.amount_contract_version != AMOUNT_CONTRACT_VERSION:
            raise EvaluationValidationError(
                "Stage 5F amount contract version mismatch",
                code="AMOUNT_CONTRACT_VERSION_MISMATCH",
            )

        input_ids = [item.input_id for item in context.calculation_inputs]
        if len(input_ids) != len(set(input_ids)):
            raise EvaluationValidationError(
                "calculation input IDs are not unique",
                code="DUPLICATE_INPUT_ID",
            )

        coverage_keys = [_coverage_key(entry) for entry in context.selector_coverage]
        if len(coverage_keys) != len(set(coverage_keys)):
            raise EvaluationValidationError(
                "selector coverage contains duplicate semantic selector entries",
                code="DUPLICATE_SELECTOR_COVERAGE",
            )

        readiness_ids = [entry.definition_id for entry in context.definition_readiness]
        if len(readiness_ids) != len(set(readiness_ids)):
            raise EvaluationValidationError(
                "definition readiness contains duplicate definition IDs",
                code="DUPLICATE_DEFINITION_READINESS",
            )

        plan_ids = [plan.definition_id for plan in plans]
        if len(plan_ids) != len(set(plan_ids)):
            raise EvaluationValidationError(
                "evaluation plans contain duplicate definition IDs",
                code="DUPLICATE_DEFINITION_PLAN",
            )
        plan_id_set = set(plan_ids)
        # Definitions that fail closed at planning time have no plan, so readiness
        # may legitimately cover more definitions than there are plans. Every plan
        # must still have readiness; extra readiness entries are the blocked cells.
        if not plan_id_set.issubset(set(readiness_ids)):
            raise EvaluationValidationError(
                "definition readiness universe differs from evaluation plans",
                code="DEFINITION_READINESS_UNIVERSE_MISMATCH",
            )

        plan_scenarios = {plan.scenario_id for plan in plans}
        input_scenarios = {item.scenario_id for item in context.calculation_inputs}
        if not input_scenarios.issubset(plan_scenarios):
            raise EvaluationValidationError(
                "calculation inputs contain scenarios outside the evaluation universe",
                code="SCENARIO_UNIVERSE_MISMATCH",
            )

        plan_owner = {plan.definition_id: plan.scenario_id for plan in plans}
        for entry in context.selector_coverage:
            expected_scenario = plan_owner.get(entry.definition_id)
            if expected_scenario is None:
                # Coverage for a definition that failed closed at planning time.
                continue
            if entry.scenario_id != expected_scenario:
                raise EvaluationValidationError(
                    "selector coverage contains an unknown or wrongly owned definition",
                    code="SELECTOR_COVERAGE_UNIVERSE_MISMATCH",
                )

        coverage_map = {_coverage_key(entry): entry for entry in context.selector_coverage}
        readiness_map = {entry.definition_id: entry for entry in context.definition_readiness}
        for plan in plans:
            readiness = readiness_map.get(plan.definition_id)
            if readiness is None:
                raise EvaluationValidationError(
                    f"definition readiness missing for {plan.definition_id}",
                    code="DEFINITION_READINESS_MISSING",
                )
            if readiness.scenario_id != plan.scenario_id:
                raise EvaluationValidationError(
                    f"definition readiness owner mismatch for {plan.definition_id}",
                    code="DEFINITION_READINESS_OWNER_MISMATCH",
                )
            for node in plan.nodes:
                if node.kind is not EvaluationNodeKind.SELECT:
                    continue
                key = _selector_key(plan, node.node_id)
                coverage_entry = coverage_map.get(key)
                if coverage_entry is None:
                    raise EvaluationValidationError(
                        f"selector coverage missing for node {node.node_id}",
                        code="SELECTOR_COVERAGE_MISSING",
                    )
                if coverage_entry.scenario_id != plan.scenario_id:
                    raise EvaluationValidationError(
                        f"selector coverage owner mismatch for node {node.node_id}",
                        code="SELECTOR_SCENARIO_OWNER_MISMATCH",
                    )

        for inp in context.calculation_inputs:
            if inp.metric_amount is not None and inp.metric_amount != inp.amount:
                raise EvaluationValidationError(
                    f"input {inp.input_id} violates amount == metric_amount",
                    code="AMOUNT_CONTRACT_VALUE_MISMATCH",
                )

    def validate_plan(
        self,
        plan: EvaluationPlan,
        context: EvaluationContext,
        *,
        roots: tuple[str, ...] | None = None,
        require_definition_readiness: bool = True,
    ) -> tuple[EvaluationIssue, ...]:
        """Return fail-closed issues for one otherwise structurally valid plan.

        ``roots`` can restrict validation to a dependency subgraph.  This is used
        for springing activation: an inactive covenant must not become unresolved
        merely because an otherwise-unused main-metric operand is unavailable.
        """

        order = PlanStructureValidator().validate(plan)
        nodes = {node.node_id: node for node in plan.nodes}
        target_roots = roots or (plan.root_node_id,)
        target_nodes: set[str] = set()
        stack = list(target_roots)
        while stack:
            current = stack.pop()
            if current in target_nodes:
                continue
            target_nodes.add(current)
            stack.extend(nodes[current].dependencies)
        coverage = {_coverage_key(entry): entry for entry in context.selector_coverage}
        readiness = {entry.definition_id: entry for entry in context.definition_readiness}[
            plan.definition_id
        ]
        scenario_inputs = tuple(
            item for item in context.calculation_inputs if item.scenario_id == plan.scenario_id
        )

        issues: list[EvaluationIssue] = []
        if require_definition_readiness and readiness.status is SelectorReadinessStatus.UNRESOLVED:
            issues.append(
                _issue(
                    "DEFINITION_INPUT_UNRESOLVED",
                    f"Stage 5F definition readiness is UNRESOLVED: {readiness.reason_code}",
                )
            )
        elif require_definition_readiness and readiness.status is not SelectorReadinessStatus.READY:
            raise EvaluationValidationError(
                f"unexpected definition readiness state: {readiness.status.value}",
                code="INVALID_DEFINITION_READINESS_STATE",
            )

        selected_inputs: dict[str, tuple[CalculationInput, ...]] = {}
        currency_sets: dict[str, frozenset[str]] = {}

        for node_id in order:
            if node_id not in target_nodes:
                continue
            node = nodes[node_id]
            if node.kind is EvaluationNodeKind.SELECT:
                assert node.selector is not None
                entry = coverage[_selector_key(plan, node_id)]
                if entry.status is SelectorReadinessStatus.UNRESOLVED:
                    issues.append(
                        _issue(
                            "SELECTOR_INPUT_UNRESOLVED",
                            f"selector source is unresolved: {entry.reason_code}",
                            node_id=node_id,
                        )
                    )

                matches: list[CalculationInput] = []
                undecidable: list[str] = []
                for inp in scenario_inputs:
                    if not input_matches_selector_strict(inp, node.selector):
                        continue
                    period_membership = input_period_membership(inp, plan.period)
                    if period_membership is None:
                        undecidable.append(inp.input_id)
                        continue
                    if period_membership:
                        matches.append(inp)
                if undecidable:
                    issues.append(
                        _issue(
                            "PERIOD_MEMBERSHIP_UNDECIDABLE",
                            "one or more relevant inputs cannot be placed in the covenant period",
                            node_id=node_id,
                            input_ids=tuple(sorted(undecidable)),
                        )
                    )
                selected_inputs[node_id] = tuple(sorted(matches, key=lambda item: item.input_id))
                currency_sets[node_id] = frozenset(item.currency for item in matches)
                continue

            if node.kind is EvaluationNodeKind.MATERIALITY_FILTER:
                parent_id = node.dependencies[0]
                parent_inputs = selected_inputs.get(parent_id, ())
                assert node.materiality_threshold is not None
                threshold_currency = node.materiality_threshold.currency
                bad = tuple(
                    sorted(
                        item.input_id
                        for item in parent_inputs
                        if item.currency != threshold_currency
                    )
                )
                if bad:
                    issues.append(
                        _issue(
                            "MIXED_CURRENCY_NO_TRUSTED_CONVERSION",
                            "materiality threshold currency differs from selected input currency",
                            node_id=node_id,
                            input_ids=bad,
                        )
                    )
                selected_inputs[node_id] = parent_inputs
                currency_sets[node_id] = frozenset(item.currency for item in parent_inputs)
                continue

            if node.kind is EvaluationNodeKind.CONSTANT:
                if (
                    node.quantity_type is QuantityType.MONEY
                    and node.constant is not None
                    and node.constant.currency is not None
                ):
                    currency_sets[node_id] = frozenset({node.constant.currency})
                else:
                    currency_sets[node_id] = frozenset()
                continue

            if node.kind in {EvaluationNodeKind.SUM, EvaluationNodeKind.COUNT}:
                parent = node.dependencies[0]
                parent_currencies = currency_sets.get(parent, frozenset())
                if node.kind is EvaluationNodeKind.SUM and len(parent_currencies) > 1:
                    parent_inputs = selected_inputs.get(parent, ())
                    issues.append(
                        _issue(
                            "MIXED_CURRENCY_NO_TRUSTED_CONVERSION",
                            "SUM contains more than one currency and no trusted conversion exists",
                            node_id=node_id,
                            input_ids=tuple(item.input_id for item in parent_inputs),
                        )
                    )
                currency_sets[node_id] = (
                    parent_currencies if node.kind is EvaluationNodeKind.SUM else frozenset()
                )
                continue

            dependency_currency_sets = [
                currency_sets.get(dependency, frozenset()) for dependency in node.dependencies
            ]

            if node.quantity_type is not QuantityType.MONEY and node.kind not in {
                EvaluationNodeKind.DIVIDE
            }:
                currency_sets[node_id] = frozenset()
                continue

            if node.kind in {EvaluationNodeKind.ADD, EvaluationNodeKind.SUBTRACT}:
                combined = frozenset().union(*dependency_currency_sets)
                if len(combined) > 1:
                    issues.append(
                        _issue(
                            "MIXED_CURRENCY_NO_TRUSTED_CONVERSION",
                            f"{node.kind.value} combines money values in different currencies",
                            node_id=node_id,
                        )
                    )
                currency_sets[node_id] = combined
                continue

            if node.kind in {EvaluationNodeKind.MIN, EvaluationNodeKind.MAX}:
                if node.quantity_type is QuantityType.MONEY:
                    combined = frozenset().union(*dependency_currency_sets)
                    if len(combined) > 1:
                        issues.append(
                            _issue(
                                "MIXED_CURRENCY_NO_TRUSTED_CONVERSION",
                                f"{node.kind.value} compares money values in different currencies",
                                node_id=node_id,
                            )
                        )
                    currency_sets[node_id] = combined
                else:
                    currency_sets[node_id] = frozenset()
                continue

            if node.kind is EvaluationNodeKind.MULTIPLY:
                money_sets = [
                    currency_set
                    for dependency, currency_set in zip(
                        node.dependencies, dependency_currency_sets, strict=True
                    )
                    if nodes[dependency].quantity_type is QuantityType.MONEY
                ]
                combined = frozenset().union(*money_sets) if money_sets else frozenset()
                if len(combined) > 1:
                    issues.append(
                        _issue(
                            "MIXED_CURRENCY_NO_TRUSTED_CONVERSION",
                            "MULTIPLY encountered incompatible money currencies",
                            node_id=node_id,
                        )
                    )
                currency_sets[node_id] = combined
                continue

            if node.kind is EvaluationNodeKind.DIVIDE:
                left, right = node.dependencies
                if (
                    nodes[left].quantity_type is QuantityType.MONEY
                    and nodes[right].quantity_type is QuantityType.MONEY
                ):
                    combined = dependency_currency_sets[0] | dependency_currency_sets[1]
                    if len(combined) > 1:
                        issues.append(
                            _issue(
                                "MIXED_CURRENCY_NO_TRUSTED_CONVERSION",
                                "money/money ratio uses different currencies "
                                "without trusted conversion",
                                node_id=node_id,
                            )
                        )
                currency_sets[node_id] = frozenset()
                continue

            currency_sets[node_id] = frozenset()

        root_currencies = currency_sets.get(plan.root_node_id, frozenset())
        if (
            plan.root_node_id in target_nodes
            and plan.metric_quantity_type is QuantityType.MONEY
            and plan.threshold.quantity_type is QuantityType.MONEY
            and root_currencies
            and plan.threshold.currency not in root_currencies
        ):
            issues.append(
                _issue(
                    "MIXED_CURRENCY_NO_TRUSTED_CONVERSION",
                    "metric currency differs from covenant threshold currency",
                    node_id=plan.root_node_id,
                )
            )

        deduped: dict[tuple[object, ...], EvaluationIssue] = {}
        for issue in issues:
            key = (
                issue.code,
                issue.node_id,
                issue.input_ids,
                issue.message,
            )
            deduped[key] = issue
        return tuple(
            sorted(
                deduped.values(),
                key=lambda item: (
                    item.code,
                    item.node_id or "",
                    item.input_ids,
                    item.message,
                ),
            )
        )
