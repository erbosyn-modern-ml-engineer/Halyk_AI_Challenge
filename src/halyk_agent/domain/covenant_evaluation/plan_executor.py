"""Execute DSL v2 covenant plans (compound logic, expression thresholds, activation).

Every number is produced by the same node executor the legacy path uses, so
selector semantics, materiality filtering and currency rules are identical. This
module only composes those numbers into boolean logic; it never computes an
amount of its own and never substitutes a value it could not resolve.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from halyk_agent.domain.covenants.ast import (
    Always,
    And,
    BoolExpr,
    Comparator,
    Compare,
    Constant,
    Expr,
    Not,
    Or,
    infer_quantity_type,
)
from halyk_agent.domain.covenants.models import CovenantDefinition, CovenantPlan
from halyk_agent.domain.covenants.quantity import QuantityType, TypedQuantity

from .executor import EvaluationExecutor
from .models import ComplianceStatus, EvaluationContext, EvaluationNumber, EvaluationStatus
from .planner import plan_definition


class PlanExecutionUnresolved(Exception):
    """A required number could not be resolved; the cell stays unresolved."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class PlanEvaluation:
    status: EvaluationStatus
    compliance_status: ComplianceStatus | None
    actual: EvaluationNumber | None
    contributing_transaction_ids: tuple[str, ...]
    reason: str | None = None


def _probe_threshold(quantity_type: QuantityType, currency: str | None) -> TypedQuantity:
    """A structurally valid throwaway threshold so one expression can be executed.

    Its value is never compared against anything — only the computed actual is read.
    """
    if quantity_type is QuantityType.MONEY:
        return TypedQuantity(
            quantity_type=QuantityType.MONEY, value=Decimal("0"), currency=currency or "USD"
        )
    if quantity_type is QuantityType.COUNT:
        return TypedQuantity(quantity_type=QuantityType.COUNT, value=Decimal("0"))
    return TypedQuantity(quantity_type=QuantityType.RATIO, value=Decimal("0"))


class CovenantPlanExecutor:
    """Evaluate a CovenantPlan by delegating every number to the node executor."""

    def __init__(self, executor: EvaluationExecutor | None = None) -> None:
        self._executor = executor or EvaluationExecutor()

    def _number(
        self,
        expr: Expr,
        *,
        definition: CovenantDefinition,
        context: EvaluationContext,
        collected: list[str],
    ) -> EvaluationNumber:
        if isinstance(expr, Constant):
            quantity = expr.quantity
            if quantity.quantity_type is QuantityType.PERCENT:
                quantity = quantity.as_ratio()
            return EvaluationNumber(
                quantity_type=quantity.quantity_type,
                value=quantity.value,
                currency=quantity.currency,
            )
        quantity_type = infer_quantity_type(expr)
        currency = definition.threshold.currency if definition.threshold else None
        probe = definition.model_copy(
            update={
                "metric": expr,
                "metric_quantity_type": quantity_type,
                "comparator": Comparator.LTE,
                "threshold": _probe_threshold(quantity_type, currency),
                "plan": None,
            }
        )
        plan = plan_definition(probe)
        result = self._executor.execute(plan, context)
        if result.status is not EvaluationStatus.RESOLVED or result.actual is None:
            codes = ",".join(sorted({issue.code for issue in result.issues})) or "UNRESOLVED"
            raise PlanExecutionUnresolved(codes)
        collected.extend(result.contributing_transaction_ids)
        return result.actual

    def _compare(
        self, left: EvaluationNumber, comparator: Comparator, right: EvaluationNumber
    ) -> bool:
        # Never compare across currencies without a trusted conversion.
        if (
            left.quantity_type is QuantityType.MONEY
            and right.quantity_type is QuantityType.MONEY
            and left.currency
            and right.currency
            and left.currency != right.currency
        ):
            raise PlanExecutionUnresolved("MIXED_CURRENCY_NO_TRUSTED_CONVERSION")
        a, b = left.value, right.value
        if comparator is Comparator.LT:
            return a < b
        if comparator is Comparator.LTE:
            return a <= b
        if comparator is Comparator.GT:
            return a > b
        if comparator is Comparator.GTE:
            return a >= b
        return a == b

    def _predicate(
        self,
        expr: BoolExpr,
        *,
        definition: CovenantDefinition,
        context: EvaluationContext,
        collected: list[str],
    ) -> bool:
        if isinstance(expr, Always):
            return True
        if isinstance(expr, Not):
            return not self._predicate(
                expr.of, definition=definition, context=context, collected=collected
            )
        if isinstance(expr, And):
            # Every leg must resolve: an unresolved leg makes the whole
            # conjunction unknown, never False.
            return all(
                self._predicate(arg, definition=definition, context=context, collected=collected)
                for arg in expr.args
            )
        if isinstance(expr, Or):
            return any(
                self._predicate(arg, definition=definition, context=context, collected=collected)
                for arg in expr.args
            )
        if isinstance(expr, Compare):
            left = self._number(
                expr.left, definition=definition, context=context, collected=collected
            )
            right = self._number(
                expr.right, definition=definition, context=context, collected=collected
            )
            return self._compare(left, expr.comparator, right)
        raise PlanExecutionUnresolved("UNSUPPORTED_PREDICATE")

    def evaluate(
        self,
        definition: CovenantDefinition,
        plan: CovenantPlan,
        context: EvaluationContext,
    ) -> PlanEvaluation:
        collected: list[str] = []
        try:
            actual = self._number(
                plan.reported_actual, definition=definition, context=context, collected=collected
            )
        except PlanExecutionUnresolved as exc:
            return PlanEvaluation(
                EvaluationStatus.UNRESOLVED, None, None, (), reason=f"ACTUAL:{exc.reason}"
            )

        # An unevaluable activation must never be treated as active: a springing
        # covenant is not breached on an unknown trigger.
        try:
            active = self._predicate(
                plan.activation_condition,
                definition=definition,
                context=context,
                collected=collected,
            )
        except PlanExecutionUnresolved as exc:
            return PlanEvaluation(
                EvaluationStatus.UNRESOLVED,
                None,
                actual,
                tuple(dict.fromkeys(collected)),
                reason=f"ACTIVATION:{exc.reason}",
            )

        evidence = tuple(dict.fromkeys(collected))
        if not active:
            # Inactive restriction: compliant, and the actual is still reported.
            return PlanEvaluation(
                EvaluationStatus.RESOLVED, ComplianceStatus.COMPLIANT, actual, evidence
            )

        try:
            breached = self._predicate(
                plan.breach_condition,
                definition=definition,
                context=context,
                collected=collected,
            )
        except PlanExecutionUnresolved as exc:
            return PlanEvaluation(
                EvaluationStatus.UNRESOLVED,
                None,
                actual,
                evidence,
                reason=f"BREACH:{exc.reason}",
            )
        return PlanEvaluation(
            EvaluationStatus.RESOLVED,
            ComplianceStatus.BREACH if breached else ComplianceStatus.COMPLIANT,
            actual,
            tuple(dict.fromkeys(collected)),
        )
