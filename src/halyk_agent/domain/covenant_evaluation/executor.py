"""Deterministic Decimal orchestration for validated covenant evaluation plans."""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Context, localcontext

from halyk_agent.domain.transaction_taxonomy.models import CalculationInput

from .constants import DECIMAL_PRECISION
from .context_validation import ContextValidator
from .models import (
    ActivationState,
    ComplianceStatus,
    CovenantEvaluationResult,
    EvaluationContext,
    EvaluationIssue,
    EvaluationIssueSeverity,
    EvaluationNode,
    EvaluationNodeResult,
    EvaluationPlan,
    EvaluationStatus,
    EvaluationTrace,
    EvaluationTraceStep,
    NodeResultStatus,
)
from .operations import _compare_values, _dedupe_issues, execute_node
from .structure_validation import PlanStructureValidator


def _trace(
    plan: EvaluationPlan,
    order: tuple[str, ...],
    nodes: dict[str, EvaluationNode],
    results: dict[str, EvaluationNodeResult],
    extra_issues: tuple[EvaluationIssue, ...] = (),
) -> EvaluationTrace:
    steps: list[EvaluationTraceStep] = []
    issue_codes: list[str] = [item.code for item in extra_issues]
    for sequence, node_id in enumerate(order):
        result = results.get(node_id)
        if result is None:
            continue
        node = nodes[node_id]
        number = result.number
        codes = tuple(item.code for item in result.issues)
        issue_codes.extend(codes)
        steps.append(
            EvaluationTraceStep(
                sequence=sequence,
                node_id=node_id,
                kind=node.kind,
                dependencies=node.dependencies,
                status=result.status,
                selected_input_ids=result.selected_input_ids,
                contributing_input_ids=result.contributing_input_ids,
                quantity_type=number.quantity_type if number is not None else None,
                value=number.value if number is not None else None,
                currency=number.currency if number is not None else None,
                issue_codes=codes,
            )
        )
    executed_order = tuple(node_id for node_id in order if node_id in results)
    return EvaluationTrace(
        plan_id=plan.plan_id,
        execution_order=executed_order,
        steps=tuple(steps),
        issue_codes=tuple(sorted(set(issue_codes))),
    )


class EvaluationExecutor:
    """Execute a validated plan with no I/O, no model calls and no ambient Decimal state."""

    def __init__(self) -> None:
        self._structure = PlanStructureValidator()
        self._context = ContextValidator()

    def execute(
        self,
        plan: EvaluationPlan,
        context: EvaluationContext,
    ) -> CovenantEvaluationResult:
        """Validate and execute one plan against a context scoped to that plan."""

        self._context.validate_global((plan,), context)
        return self._execute_after_global(plan, context)

    def execute_many(
        self,
        plans: tuple[EvaluationPlan, ...],
        context: EvaluationContext,
    ) -> tuple[CovenantEvaluationResult, ...]:
        """Validate one shared Stage 5F universe once, then evaluate every plan."""

        self._context.validate_global(plans, context)
        return tuple(self._execute_after_global(plan, context) for plan in plans)

    @staticmethod
    def _reachable(
        nodes: dict[str, EvaluationNode],
        root: str,
    ) -> set[str]:
        reachable: set[str] = set()
        stack = [root]
        while stack:
            current = stack.pop()
            if current in reachable:
                continue
            reachable.add(current)
            stack.extend(nodes[current].dependencies)
        return reachable

    def _run_nodes(
        self,
        *,
        order: tuple[str, ...],
        target_nodes: set[str],
        nodes: dict[str, EvaluationNode],
        plan: EvaluationPlan,
        scenario_inputs: tuple[CalculationInput, ...],
        inputs_by_id: dict[str, CalculationInput],
        results: dict[str, EvaluationNodeResult],
    ) -> None:
        for node_id in order:
            if node_id not in target_nodes or node_id in results:
                continue
            node = nodes[node_id]
            results[node_id] = execute_node(
                node,
                plan,
                scenario_inputs,
                inputs_by_id,
                results,
            )

    def _base_result(
        self,
        plan: EvaluationPlan,
        *,
        status: EvaluationStatus,
        activation_state: ActivationState,
        issues: tuple[EvaluationIssue, ...],
        trace: EvaluationTrace,
        contributing_input_ids: tuple[str, ...] = (),
        inputs_by_id: dict[str, CalculationInput] | None = None,
    ) -> CovenantEvaluationResult:
        input_map = inputs_by_id or {}
        return CovenantEvaluationResult(
            definition_id=plan.definition_id,
            scenario_id=plan.scenario_id,
            clause_id=plan.clause_id,
            family_id=plan.family_id,
            status=status,
            activation_state=activation_state,
            threshold=plan.threshold,
            contributing_input_ids=contributing_input_ids,
            contributing_transaction_ids=self._transaction_ids(
                contributing_input_ids,
                input_map,
            ),
            issues=issues,
            trace=trace,
        )

    def _execute_after_global(
        self,
        plan: EvaluationPlan,
        context: EvaluationContext,
    ) -> CovenantEvaluationResult:
        order = self._structure.validate(plan)
        nodes = {node.node_id: node for node in plan.nodes}
        scenario_inputs = tuple(
            item for item in context.calculation_inputs if item.scenario_id == plan.scenario_id
        )
        inputs_by_id = {item.input_id: item for item in scenario_inputs}
        results: dict[str, EvaluationNodeResult] = {}
        activation_state = ActivationState.NOT_APPLICABLE

        decimal_context = Context(prec=DECIMAL_PRECISION, rounding=ROUND_HALF_EVEN)
        with localcontext(decimal_context):
            if plan.activation_root_node_id is not None:
                activation_issues = self._context.validate_plan(
                    plan,
                    context,
                    roots=(plan.activation_root_node_id,),
                    require_definition_readiness=False,
                )
                if activation_issues:
                    trace = _trace(plan, order, nodes, results, activation_issues)
                    return self._base_result(
                        plan,
                        status=EvaluationStatus.UNRESOLVED,
                        activation_state=ActivationState.UNRESOLVED,
                        issues=activation_issues,
                        trace=trace,
                    )

                activation_nodes = self._reachable(nodes, plan.activation_root_node_id)
                self._run_nodes(
                    order=order,
                    target_nodes=activation_nodes,
                    nodes=nodes,
                    plan=plan,
                    scenario_inputs=scenario_inputs,
                    inputs_by_id=inputs_by_id,
                    results=results,
                )
                activation_result = results[plan.activation_root_node_id]
                activation_node_issues = _dedupe_issues(
                    [issue for node_id in activation_nodes for issue in results[node_id].issues]
                )
                if activation_result.status is NodeResultStatus.ERROR:
                    trace = _trace(plan, order, nodes, results)
                    return self._base_result(
                        plan,
                        status=EvaluationStatus.ERROR,
                        activation_state=ActivationState.ERROR,
                        issues=activation_node_issues,
                        trace=trace,
                        contributing_input_ids=activation_result.contributing_input_ids,
                        inputs_by_id=inputs_by_id,
                    )
                if (
                    activation_result.status is NodeResultStatus.UNRESOLVED
                    or activation_result.number is None
                ):
                    trace = _trace(plan, order, nodes, results)
                    return self._base_result(
                        plan,
                        status=EvaluationStatus.UNRESOLVED,
                        activation_state=ActivationState.UNRESOLVED,
                        issues=activation_node_issues,
                        trace=trace,
                        contributing_input_ids=activation_result.contributing_input_ids,
                        inputs_by_id=inputs_by_id,
                    )

                assert plan.activation_comparator is not None
                assert plan.activation_threshold is not None
                active, _bound_activation, compare_issue = _compare_values(
                    activation_result.number,
                    plan.activation_comparator,
                    plan.activation_threshold,
                )
                activation_issue_list = list(activation_node_issues)
                if compare_issue is not None:
                    activation_issue_list.append(compare_issue)
                activation_issue_tuple = _dedupe_issues(activation_issue_list)
                if compare_issue is not None or active is None:
                    trace = _trace(
                        plan,
                        order,
                        nodes,
                        results,
                        activation_issue_tuple,
                    )
                    return self._base_result(
                        plan,
                        status=(
                            EvaluationStatus.ERROR
                            if compare_issue is not None
                            and compare_issue.severity is EvaluationIssueSeverity.ERROR
                            else EvaluationStatus.UNRESOLVED
                        ),
                        activation_state=ActivationState.UNRESOLVED,
                        issues=activation_issue_tuple,
                        trace=trace,
                        contributing_input_ids=activation_result.contributing_input_ids,
                        inputs_by_id=inputs_by_id,
                    )
                activation_state = ActivationState.ACTIVE if active else ActivationState.INACTIVE

            main_issues = self._context.validate_plan(plan, context)
            if main_issues:
                trace = _trace(plan, order, nodes, results, main_issues)
                return self._base_result(
                    plan,
                    status=EvaluationStatus.UNRESOLVED,
                    activation_state=activation_state,
                    issues=main_issues,
                    trace=trace,
                )

            main_nodes = self._reachable(nodes, plan.root_node_id)
            self._run_nodes(
                order=order,
                target_nodes=main_nodes,
                nodes=nodes,
                plan=plan,
                scenario_inputs=scenario_inputs,
                inputs_by_id=inputs_by_id,
                results=results,
            )
            root = results[plan.root_node_id]
            executed_issues = _dedupe_issues(
                [issue for result in results.values() for issue in result.issues]
            )
            trace = _trace(plan, order, nodes, results)
            if root.status is NodeResultStatus.ERROR:
                return self._base_result(
                    plan,
                    status=EvaluationStatus.ERROR,
                    activation_state=activation_state,
                    issues=executed_issues,
                    trace=trace,
                    contributing_input_ids=root.contributing_input_ids,
                    inputs_by_id=inputs_by_id,
                )
            if root.status is NodeResultStatus.UNRESOLVED or root.number is None:
                return self._base_result(
                    plan,
                    status=EvaluationStatus.UNRESOLVED,
                    activation_state=activation_state,
                    issues=executed_issues,
                    trace=trace,
                    contributing_input_ids=root.contributing_input_ids,
                    inputs_by_id=inputs_by_id,
                )

            compliant, actual, compare_issue = _compare_values(
                root.number,
                plan.comparator,
                plan.threshold,
            )
            final_issues = list(executed_issues)
            if compare_issue is not None:
                final_issues.append(compare_issue)
            final_issue_tuple = _dedupe_issues(final_issues)
            if compare_issue is not None or compliant is None:
                return self._base_result(
                    plan,
                    status=(
                        EvaluationStatus.ERROR
                        if compare_issue is not None
                        and compare_issue.severity is EvaluationIssueSeverity.ERROR
                        else EvaluationStatus.UNRESOLVED
                    ),
                    activation_state=activation_state,
                    issues=final_issue_tuple,
                    trace=trace,
                    contributing_input_ids=root.contributing_input_ids,
                    inputs_by_id=inputs_by_id,
                )

            if activation_state is ActivationState.INACTIVE:
                return CovenantEvaluationResult(
                    definition_id=plan.definition_id,
                    scenario_id=plan.scenario_id,
                    clause_id=plan.clause_id,
                    family_id=plan.family_id,
                    status=EvaluationStatus.NOT_ACTIVATED,
                    compliance_status=None,
                    activation_state=ActivationState.INACTIVE,
                    actual=actual,
                    threshold=plan.threshold,
                    contributing_input_ids=root.contributing_input_ids,
                    contributing_transaction_ids=self._transaction_ids(
                        root.contributing_input_ids,
                        inputs_by_id,
                    ),
                    issues=final_issue_tuple,
                    trace=trace,
                )

            return CovenantEvaluationResult(
                definition_id=plan.definition_id,
                scenario_id=plan.scenario_id,
                clause_id=plan.clause_id,
                family_id=plan.family_id,
                status=EvaluationStatus.RESOLVED,
                compliance_status=(
                    ComplianceStatus.COMPLIANT if compliant else ComplianceStatus.BREACH
                ),
                activation_state=activation_state,
                actual=actual,
                threshold=plan.threshold,
                contributing_input_ids=root.contributing_input_ids,
                contributing_transaction_ids=self._transaction_ids(
                    root.contributing_input_ids,
                    inputs_by_id,
                ),
                issues=final_issue_tuple,
                trace=trace,
            )

    @staticmethod
    def _transaction_ids(
        input_ids: tuple[str, ...],
        inputs_by_id: dict[str, CalculationInput],
    ) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    transaction_id
                    for input_id in input_ids
                    if input_id in inputs_by_id
                    if (transaction_id := inputs_by_id[input_id].transaction_id) is not None
                }
            )
        )
