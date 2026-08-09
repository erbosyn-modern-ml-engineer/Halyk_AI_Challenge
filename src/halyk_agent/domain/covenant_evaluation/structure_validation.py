"""Structural checks for Stage 6 evaluation plans."""

from __future__ import annotations

import heapq
from collections import defaultdict

from halyk_agent.domain.covenants.models import CovenantModifierKind
from halyk_agent.domain.covenants.quantity import QuantityType

from .models import EvaluationNode, EvaluationNodeKind, EvaluationPlan


class EvaluationValidationError(ValueError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


_SET_KINDS = {EvaluationNodeKind.SELECT, EvaluationNodeKind.MATERIALITY_FILTER}
_NUMERIC_KINDS = {
    EvaluationNodeKind.CONSTANT,
    EvaluationNodeKind.SUM,
    EvaluationNodeKind.COUNT,
    EvaluationNodeKind.MIN,
    EvaluationNodeKind.MAX,
    EvaluationNodeKind.ADD,
    EvaluationNodeKind.SUBTRACT,
    EvaluationNodeKind.MULTIPLY,
    EvaluationNodeKind.DIVIDE,
}
_ALLOWED_MODIFIERS = {
    CovenantModifierKind.AUDITOR_RECLASSIFICATION_INCLUDE,
    CovenantModifierKind.AUDITOR_RECLASSIFICATION_EXCLUDE,
    CovenantModifierKind.REJECTED_RECLASSIFICATION_EXCLUDE,
    CovenantModifierKind.BOTH_NUMERATOR_AND_DENOMINATOR_RECLASS,
    CovenantModifierKind.MATERIALITY_FLOOR,
}


def _compatible_threshold(metric_type: QuantityType, threshold_type: QuantityType) -> bool:
    if metric_type is QuantityType.RATIO:
        return threshold_type in {QuantityType.RATIO, QuantityType.PERCENT}
    return metric_type is threshold_type


def _numeric_type(node: EvaluationNode) -> QuantityType:
    if node.quantity_type is None:
        raise EvaluationValidationError(
            f"numeric node {node.node_id} has no quantity_type",
            code="NODE_QUANTITY_TYPE_MISSING",
        )
    return node.quantity_type


class PlanStructureValidator:
    """Reject broken plan graphs before execution.

    Missing dependencies are errors here. Treating them as outside inputs would
    make a bad plan look valid and produce a result we cannot explain later.
    """

    def validate(self, plan: EvaluationPlan) -> tuple[str, ...]:
        node_ids = [node.node_id for node in plan.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise EvaluationValidationError(
                "evaluation plan contains duplicate node IDs",
                code="DUPLICATE_NODE_ID",
            )
        nodes = {node.node_id: node for node in plan.nodes}
        if plan.root_node_id not in nodes:
            raise EvaluationValidationError(
                f"root node is missing: {plan.root_node_id}",
                code="MISSING_ROOT",
            )
        if plan.activation_root_node_id is not None and plan.activation_root_node_id not in nodes:
            raise EvaluationValidationError(
                f"activation root is missing: {plan.activation_root_node_id}",
                code="MISSING_ACTIVATION_ROOT",
            )

        for modifier in plan.modifiers:
            if modifier.kind not in _ALLOWED_MODIFIERS:
                raise EvaluationValidationError(
                    f"unsupported modifier: {modifier.kind.value}",
                    code="UNSUPPORTED_MODIFIER",
                )
            if modifier.kind is CovenantModifierKind.MATERIALITY_FLOOR:
                if modifier.threshold is None:
                    raise EvaluationValidationError(
                        "MATERIALITY_FLOOR is missing threshold",
                        code="MATERIALITY_THRESHOLD_MISSING",
                    )
                if modifier.threshold.quantity_type is not QuantityType.MONEY:
                    raise EvaluationValidationError(
                        "MATERIALITY_FLOOR threshold is not MONEY",
                        code="MATERIALITY_THRESHOLD_TYPE",
                    )

        for node in plan.nodes:
            if len(node.dependencies) != len(set(node.dependencies)):
                raise EvaluationValidationError(
                    f"node {node.node_id} contains duplicate dependencies",
                    code="DUPLICATE_DEPENDENCY",
                )
            for dependency in node.dependencies:
                if dependency not in nodes:
                    raise EvaluationValidationError(
                        f"node {node.node_id} depends on missing node {dependency}",
                        code="MISSING_DEPENDENCY",
                    )
                if dependency == node.node_id:
                    raise EvaluationValidationError(
                        f"node {node.node_id} depends on itself",
                        code="SELF_DEPENDENCY",
                    )
            self._validate_payload(node, nodes)

        order = self._topological_order(plan.nodes)

        roots = [plan.root_node_id]
        if plan.activation_root_node_id is not None:
            roots.append(plan.activation_root_node_id)
        reachable: set[str] = set()
        stack = list(roots)
        while stack:
            current = stack.pop()
            if current in reachable:
                continue
            reachable.add(current)
            stack.extend(nodes[current].dependencies)
        orphans = sorted(set(nodes) - reachable)
        if orphans:
            raise EvaluationValidationError(
                f"evaluation plan contains orphan nodes: {orphans}",
                code="ORPHAN_NODE",
            )

        root_type = _numeric_type(nodes[plan.root_node_id])
        if root_type is not plan.metric_quantity_type:
            raise EvaluationValidationError(
                "root quantity type disagrees with compiled metric quantity type",
                code="ROOT_TYPE_MISMATCH",
            )
        if not _compatible_threshold(root_type, plan.threshold.quantity_type):
            raise EvaluationValidationError(
                "covenant threshold type is incompatible with metric type",
                code="THRESHOLD_TYPE_MISMATCH",
            )

        if plan.activation_root_node_id is None:
            if plan.activation_comparator is not None or plan.activation_threshold is not None:
                raise EvaluationValidationError(
                    "activation comparator/threshold present without activation root",
                    code="ACTIVATION_PAYLOAD_MISMATCH",
                )
        else:
            if plan.activation_comparator is None or plan.activation_threshold is None:
                raise EvaluationValidationError(
                    "activation root is missing comparator/threshold",
                    code="ACTIVATION_PAYLOAD_MISMATCH",
                )
            activation_type = _numeric_type(nodes[plan.activation_root_node_id])
            if not _compatible_threshold(activation_type, plan.activation_threshold.quantity_type):
                raise EvaluationValidationError(
                    "activation threshold type is incompatible with activation metric",
                    code="ACTIVATION_TYPE_MISMATCH",
                )

        return order

    def _validate_payload(
        self,
        node: EvaluationNode,
        nodes: dict[str, EvaluationNode],
    ) -> None:
        kind = node.kind
        if kind is EvaluationNodeKind.SELECT:
            if node.dependencies or node.selector is None:
                raise EvaluationValidationError(
                    f"SELECT node {node.node_id} has invalid payload",
                    code="INVALID_SELECT_PAYLOAD",
                )
            if any(
                item is not None
                for item in (
                    node.quantity_type,
                    node.constant,
                    node.materiality_threshold,
                    node.materiality_category,
                )
            ):
                raise EvaluationValidationError(
                    f"SELECT node {node.node_id} carries unexpected numeric payload",
                    code="INVALID_SELECT_PAYLOAD",
                )
            return

        if kind is EvaluationNodeKind.MATERIALITY_FILTER:
            if (
                len(node.dependencies) != 1
                or node.materiality_threshold is None
                or node.materiality_category is None
                or node.quantity_type is not None
                or node.constant is not None
            ):
                raise EvaluationValidationError(
                    f"MATERIALITY_FILTER node {node.node_id} has invalid payload",
                    code="INVALID_MATERIALITY_FILTER_PAYLOAD",
                )
            parent = nodes[node.dependencies[0]]
            if parent.kind is not EvaluationNodeKind.SELECT:
                raise EvaluationValidationError(
                    "MATERIALITY_FILTER must consume a SELECT node directly",
                    code="INVALID_MATERIALITY_FILTER_PARENT",
                )
            if parent.selector is None or parent.selector.category is not node.materiality_category:
                raise EvaluationValidationError(
                    "MATERIALITY_FILTER category disagrees with selector",
                    code="MATERIALITY_FILTER_CATEGORY_MISMATCH",
                )
            if node.materiality_threshold.quantity_type is not QuantityType.MONEY:
                raise EvaluationValidationError(
                    "MATERIALITY_FILTER threshold is not MONEY",
                    code="MATERIALITY_THRESHOLD_TYPE",
                )
            return

        if kind is EvaluationNodeKind.CONSTANT:
            if (
                node.dependencies
                or node.constant is None
                or node.quantity_type is None
                or node.constant.quantity_type is not node.quantity_type
                or node.selector is not None
                or node.materiality_threshold is not None
                or node.materiality_category is not None
            ):
                raise EvaluationValidationError(
                    f"CONSTANT node {node.node_id} has invalid payload",
                    code="INVALID_CONSTANT_PAYLOAD",
                )
            return

        if kind not in _NUMERIC_KINDS:
            raise EvaluationValidationError(
                f"unsupported node kind: {kind.value}",
                code="UNSUPPORTED_NODE_KIND",
            )
        if (
            node.selector is not None
            or node.constant is not None
            or node.materiality_threshold is not None
            or node.materiality_category is not None
        ):
            raise EvaluationValidationError(
                f"numeric node {node.node_id} carries unexpected payload",
                code="INVALID_NODE_PAYLOAD",
            )

        qtype = _numeric_type(node)
        dependencies = [nodes[dependency] for dependency in node.dependencies]

        if kind in {EvaluationNodeKind.SUM, EvaluationNodeKind.COUNT}:
            if len(dependencies) != 1 or dependencies[0].kind not in _SET_KINDS:
                raise EvaluationValidationError(
                    f"{kind.value} must consume exactly one set-valued node",
                    code="INVALID_AGGREGATION_DEPENDENCY",
                )
            expected = QuantityType.MONEY if kind is EvaluationNodeKind.SUM else QuantityType.COUNT
            if qtype is not expected:
                raise EvaluationValidationError(
                    f"{kind.value} has wrong quantity type",
                    code="NODE_QUANTITY_TYPE_MISMATCH",
                )
            return

        if kind in {EvaluationNodeKind.MIN, EvaluationNodeKind.MAX}:
            if len(dependencies) < 2 or any(dep.kind in _SET_KINDS for dep in dependencies):
                raise EvaluationValidationError(
                    f"{kind.value} requires at least two numeric dependencies",
                    code="INVALID_EXTREMUM_DEPENDENCY",
                )
            dep_types = {_numeric_type(dep) for dep in dependencies}
            if len(dep_types) != 1 or next(iter(dep_types)) is not qtype:
                raise EvaluationValidationError(
                    f"{kind.value} dependencies have incompatible types",
                    code="NODE_QUANTITY_TYPE_MISMATCH",
                )
            return

        if len(dependencies) != 2 or any(dep.kind in _SET_KINDS for dep in dependencies):
            raise EvaluationValidationError(
                f"{kind.value} requires exactly two numeric dependencies",
                code="INVALID_ARITHMETIC_DEPENDENCY",
            )
        left_type = _numeric_type(dependencies[0])
        right_type = _numeric_type(dependencies[1])

        if kind in {EvaluationNodeKind.ADD, EvaluationNodeKind.SUBTRACT}:
            allowed = {QuantityType.MONEY, QuantityType.RATIO, QuantityType.COUNT}
            if left_type is not right_type or left_type is not qtype or left_type not in allowed:
                raise EvaluationValidationError(
                    f"{kind.value} operands have incompatible types",
                    code="NODE_QUANTITY_TYPE_MISMATCH",
                )
            return

        if kind is EvaluationNodeKind.MULTIPLY:
            valid = (
                left_type is QuantityType.RATIO
                and right_type in {QuantityType.MONEY, QuantityType.RATIO}
            ) or (
                right_type is QuantityType.RATIO
                and left_type in {QuantityType.MONEY, QuantityType.RATIO}
            )
            expected = (
                QuantityType.MONEY
                if QuantityType.MONEY in {left_type, right_type}
                else QuantityType.RATIO
            )
            if not valid or qtype is not expected:
                raise EvaluationValidationError(
                    "MULTIPLY operands have incompatible types",
                    code="NODE_QUANTITY_TYPE_MISMATCH",
                )
            return

        if kind is EvaluationNodeKind.DIVIDE and (
            left_type is not right_type
            or left_type not in {QuantityType.MONEY, QuantityType.RATIO}
            or qtype is not QuantityType.RATIO
        ):
            raise EvaluationValidationError(
                "DIVIDE requires MONEY/MONEY or RATIO/RATIO operands",
                code="NODE_QUANTITY_TYPE_MISMATCH",
            )

    @staticmethod
    def _topological_order(nodes: tuple[EvaluationNode, ...]) -> tuple[str, ...]:
        in_degree = {node.node_id: len(node.dependencies) for node in nodes}
        downstream: dict[str, list[str]] = defaultdict(list)
        for node in nodes:
            for dependency in node.dependencies:
                downstream[dependency].append(node.node_id)
        for values in downstream.values():
            values.sort()

        heap = [node_id for node_id, degree in in_degree.items() if degree == 0]
        heapq.heapify(heap)
        order: list[str] = []
        while heap:
            current = heapq.heappop(heap)
            order.append(current)
            for child in downstream.get(current, []):
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    heapq.heappush(heap, child)
        if len(order) != len(nodes):
            cyclic = sorted(node_id for node_id, degree in in_degree.items() if degree > 0)
            raise EvaluationValidationError(
                f"cycle detected in evaluation plan: {cyclic}",
                code="CYCLE_DETECTED",
            )
        return tuple(order)
