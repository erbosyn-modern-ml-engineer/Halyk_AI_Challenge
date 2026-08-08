"""Planning and structural validation tests for Stage 6."""

from __future__ import annotations

from decimal import Decimal

import pytest

from halyk_agent.domain.covenant_evaluation import (
    EvaluationNode,
    EvaluationNodeKind,
    EvaluationValidationError,
    PlanStructureValidator,
    plan_definition,
)
from halyk_agent.domain.covenants.ast import MetricCategory, Sum, TransactionSet
from halyk_agent.domain.covenants.models import CovenantModifier, CovenantModifierKind
from halyk_agent.domain.covenants.quantity import QuantityType, TypedQuantity

from ._helpers import _definition, _ratio_definition, _selector


def test_planner_is_deterministic_and_materiality_is_explicit_filter() -> None:
    selector = _selector(MetricCategory.ONE_TIME_ADD_BACKS)
    definition = _definition(
        Sum(of=TransactionSet(selector=selector)),
        selectors=(selector,),
        modifiers=(
            CovenantModifier(
                kind=CovenantModifierKind.MATERIALITY_FLOOR,
                detail="one-time items below the floor are excluded",
                threshold=TypedQuantity(
                    quantity_type=QuantityType.MONEY,
                    value=Decimal("300000"),
                    currency="USD",
                ),
                applies_to_category=MetricCategory.ONE_TIME_ADD_BACKS,
            ),
        ),
    )
    first = plan_definition(definition)
    second = plan_definition(definition)
    assert first == second
    assert first.model_dump_json() == second.model_dump_json()
    kinds = [node.kind for node in first.nodes]
    assert EvaluationNodeKind.SELECT in kinds
    assert EvaluationNodeKind.MATERIALITY_FILTER in kinds
    assert EvaluationNodeKind.SUM in kinds
    order = PlanStructureValidator().validate(first)
    assert order[-1] == first.root_node_id


def test_missing_dependency_fails_closed() -> None:
    selector = _selector()
    definition = _definition(
        Sum(of=TransactionSet(selector=selector)),
        selectors=(selector,),
    )
    plan = plan_definition(definition)
    root = next(node for node in plan.nodes if node.node_id == plan.root_node_id)
    broken_root = root.model_copy(update={"dependencies": ("missing-node",)})
    broken = plan.model_copy(
        update={
            "nodes": tuple(
                broken_root if node.node_id == root.node_id else node for node in plan.nodes
            )
        }
    )
    with pytest.raises(EvaluationValidationError) as exc:
        PlanStructureValidator().validate(broken)
    assert exc.value.code == "MISSING_DEPENDENCY"


def test_cycle_fails_closed() -> None:
    definition = _ratio_definition("2")
    base = plan_definition(definition)
    constant = EvaluationNode(
        node_id="constant",
        kind=EvaluationNodeKind.CONSTANT,
        quantity_type=QuantityType.RATIO,
        constant=TypedQuantity(quantity_type=QuantityType.RATIO, value=Decimal("1")),
    )
    left = EvaluationNode(
        node_id="left",
        kind=EvaluationNodeKind.ADD,
        dependencies=("right", "constant"),
        quantity_type=QuantityType.RATIO,
    )
    right = EvaluationNode(
        node_id="right",
        kind=EvaluationNodeKind.ADD,
        dependencies=("left", "constant"),
        quantity_type=QuantityType.RATIO,
    )
    cyclic = base.model_copy(
        update={
            "nodes": (constant, left, right),
            "root_node_id": "left",
            "metric_quantity_type": QuantityType.RATIO,
            "threshold": TypedQuantity(
                quantity_type=QuantityType.RATIO,
                value=Decimal("0"),
            ),
        }
    )
    with pytest.raises(EvaluationValidationError) as exc:
        PlanStructureValidator().validate(cyclic)
    assert exc.value.code == "CYCLE_DETECTED"


def test_divide_rejects_count_operands_even_when_types_match() -> None:
    base = plan_definition(_ratio_definition("2"))
    left = EvaluationNode(
        node_id="left-count",
        kind=EvaluationNodeKind.CONSTANT,
        quantity_type=QuantityType.COUNT,
        constant=TypedQuantity(quantity_type=QuantityType.COUNT, value=Decimal("2")),
    )
    right = EvaluationNode(
        node_id="right-count",
        kind=EvaluationNodeKind.CONSTANT,
        quantity_type=QuantityType.COUNT,
        constant=TypedQuantity(quantity_type=QuantityType.COUNT, value=Decimal("1")),
    )
    root = EvaluationNode(
        node_id="root",
        kind=EvaluationNodeKind.DIVIDE,
        dependencies=(left.node_id, right.node_id),
        quantity_type=QuantityType.RATIO,
    )
    invalid = base.model_copy(
        update={
            "nodes": (left, right, root),
            "root_node_id": root.node_id,
            "metric_quantity_type": QuantityType.RATIO,
        }
    )
    with pytest.raises(EvaluationValidationError) as exc:
        PlanStructureValidator().validate(invalid)
    assert exc.value.code == "NODE_QUANTITY_TYPE_MISMATCH"
