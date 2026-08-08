"""Pure node operations and comparison helpers for covenant evaluation."""

from __future__ import annotations

from decimal import Decimal

from halyk_agent.domain.covenants.models import Comparator
from halyk_agent.domain.covenants.quantity import QuantityType, TypedQuantity
from halyk_agent.domain.transaction_taxonomy.models import CalculationInput

from .models import (
    EvaluationIssue,
    EvaluationIssueSeverity,
    EvaluationNode,
    EvaluationNodeKind,
    EvaluationNodeResult,
    EvaluationNumber,
    EvaluationPlan,
    NodeResultStatus,
)
from .selection import input_matches_selector_strict, input_period_membership


def _issue(
    code: str,
    severity: EvaluationIssueSeverity,
    message: str,
    *,
    node_id: str | None = None,
    input_ids: tuple[str, ...] = (),
) -> EvaluationIssue:
    return EvaluationIssue(
        code=code,
        severity=severity,
        message=message,
        node_id=node_id,
        input_ids=input_ids,
    )


def _dedupe_issues(issues: list[EvaluationIssue]) -> tuple[EvaluationIssue, ...]:
    unique: dict[tuple[object, ...], EvaluationIssue] = {}
    for issue in issues:
        key = (
            issue.code,
            issue.severity,
            issue.message,
            issue.node_id,
            issue.input_ids,
            issue.evidence_refs,
        )
        unique[key] = issue
    return tuple(
        sorted(
            unique.values(),
            key=lambda item: (
                item.severity.value,
                item.code,
                item.node_id or "",
                item.input_ids,
                item.message,
            ),
        )
    )


def _typed_to_number(value: TypedQuantity) -> EvaluationNumber:
    return EvaluationNumber(
        quantity_type=value.quantity_type,
        value=value.value,
        currency=value.currency,
    )


def _ratio_value(number: EvaluationNumber) -> Decimal:
    if number.quantity_type is QuantityType.RATIO:
        return number.value
    if number.quantity_type is QuantityType.PERCENT:
        return number.value / Decimal("100")
    raise ValueError(f"{number.quantity_type.value} is not ratio-like")


def _threshold_number(
    actual: EvaluationNumber,
    threshold: TypedQuantity,
) -> EvaluationNumber:
    if (
        actual.quantity_type is QuantityType.RATIO
        and threshold.quantity_type is QuantityType.PERCENT
    ):
        normalized = threshold.as_ratio()
        return _typed_to_number(normalized)
    if (
        actual.quantity_type is QuantityType.PERCENT
        and threshold.quantity_type is QuantityType.RATIO
    ):
        return EvaluationNumber(
            quantity_type=QuantityType.PERCENT,
            value=threshold.value * Decimal("100"),
        )
    return _typed_to_number(threshold)


def _compare_values(
    actual: EvaluationNumber,
    comparator: Comparator,
    threshold: TypedQuantity,
) -> tuple[bool | None, EvaluationNumber, EvaluationIssue | None]:
    target = _threshold_number(actual, threshold)
    if actual.quantity_type is not target.quantity_type:
        return (
            None,
            actual,
            _issue(
                "COMPARISON_TYPE_MISMATCH",
                EvaluationIssueSeverity.ERROR,
                "actual and threshold quantity types are incompatible",
            ),
        )

    bound_actual = actual
    if actual.quantity_type is QuantityType.MONEY:
        if actual.currency is None and actual.zero_currency_identity:
            assert target.currency is not None
            bound_actual = actual.model_copy(
                update={
                    "currency": target.currency,
                    "zero_currency_identity": False,
                }
            )
        if bound_actual.currency != target.currency:
            return (
                None,
                bound_actual,
                _issue(
                    "MIXED_CURRENCY_NO_TRUSTED_CONVERSION",
                    EvaluationIssueSeverity.UNRESOLVED,
                    "actual and threshold currencies differ and no trusted conversion exists",
                ),
            )

    left = bound_actual.value
    right = target.value
    if comparator is Comparator.LT:
        return left < right, bound_actual, None
    if comparator is Comparator.LTE:
        return left <= right, bound_actual, None
    if comparator is Comparator.GT:
        return left > right, bound_actual, None
    if comparator is Comparator.GTE:
        return left >= right, bound_actual, None
    if comparator is Comparator.EQ:
        return left == right, bound_actual, None
    return (
        None,
        bound_actual,
        _issue(
            "UNSUPPORTED_COMPARATOR",
            EvaluationIssueSeverity.ERROR,
            f"unsupported comparator: {comparator}",
        ),
    )


def _combine_input_ids(*items: tuple[str, ...]) -> tuple[str, ...]:
    values: set[str] = set()
    for group in items:
        values.update(group)
    return tuple(sorted(values))


def _money_currency(
    numbers: tuple[EvaluationNumber, ...],
    *,
    node_id: str,
) -> tuple[str | None, EvaluationIssue | None]:
    currencies = {
        item.currency
        for item in numbers
        if item.quantity_type is QuantityType.MONEY and item.currency is not None
    }
    if len(currencies) > 1:
        return (
            None,
            _issue(
                "MIXED_CURRENCY_NO_TRUSTED_CONVERSION",
                EvaluationIssueSeverity.UNRESOLVED,
                "money operands use different currencies and no trusted conversion exists",
                node_id=node_id,
            ),
        )
    return (next(iter(currencies)) if currencies else None), None


def _numeric_failure(
    node: EvaluationNode,
    dependencies: tuple[EvaluationNodeResult, ...],
) -> EvaluationNodeResult | None:
    failed = [item for item in dependencies if item.status is not NodeResultStatus.RESOLVED]
    if not failed:
        return None
    status = (
        NodeResultStatus.ERROR
        if any(item.status is NodeResultStatus.ERROR for item in failed)
        else NodeResultStatus.UNRESOLVED
    )
    issues: list[EvaluationIssue] = []
    for item in failed:
        issues.extend(item.issues)
    return EvaluationNodeResult(
        node_id=node.node_id,
        status=status,
        contributing_input_ids=_combine_input_ids(
            *(item.contributing_input_ids for item in dependencies)
        ),
        issues=_dedupe_issues(issues),
    )


def execute_node(
    node: EvaluationNode,
    plan: EvaluationPlan,
    scenario_inputs: tuple[CalculationInput, ...],
    inputs_by_id: dict[str, CalculationInput],
    results: dict[str, EvaluationNodeResult],
) -> EvaluationNodeResult:
    dependencies = tuple(results[dependency] for dependency in node.dependencies)
    failure = _numeric_failure(node, dependencies)
    if failure is not None:
        return failure

    if node.kind is EvaluationNodeKind.SELECT:
        assert node.selector is not None
        matches: list[str] = []
        undecidable: list[str] = []
        for inp in scenario_inputs:
            if not input_matches_selector_strict(inp, node.selector):
                continue
            membership = input_period_membership(inp, plan.period)
            if membership is None:
                undecidable.append(inp.input_id)
            elif membership:
                matches.append(inp.input_id)
        if undecidable:
            issue = _issue(
                "PERIOD_MEMBERSHIP_UNDECIDABLE",
                EvaluationIssueSeverity.UNRESOLVED,
                "relevant selector input has undecidable period membership",
                node_id=node.node_id,
                input_ids=tuple(sorted(undecidable)),
            )
            return EvaluationNodeResult(
                node_id=node.node_id,
                status=NodeResultStatus.UNRESOLVED,
                issues=(issue,),
            )
        selected = tuple(sorted(matches))
        return EvaluationNodeResult(
            node_id=node.node_id,
            status=NodeResultStatus.RESOLVED,
            selected_input_ids=selected,
            contributing_input_ids=selected,
        )

    if node.kind is EvaluationNodeKind.MATERIALITY_FILTER:
        parent = dependencies[0]
        assert node.materiality_threshold is not None
        threshold = node.materiality_threshold
        assert threshold.currency is not None
        survivors: list[str] = []
        bad_currency: list[str] = []
        for input_id in parent.selected_input_ids:
            inp = inputs_by_id[input_id]
            if inp.currency != threshold.currency:
                bad_currency.append(input_id)
                continue
            if inp.amount >= threshold.value:
                survivors.append(input_id)
        if bad_currency:
            issue = _issue(
                "MIXED_CURRENCY_NO_TRUSTED_CONVERSION",
                EvaluationIssueSeverity.UNRESOLVED,
                "materiality filter cannot compare different currencies",
                node_id=node.node_id,
                input_ids=tuple(sorted(bad_currency)),
            )
            return EvaluationNodeResult(
                node_id=node.node_id,
                status=NodeResultStatus.UNRESOLVED,
                contributing_input_ids=parent.contributing_input_ids,
                issues=(issue,),
            )
        selected = tuple(sorted(survivors))
        return EvaluationNodeResult(
            node_id=node.node_id,
            status=NodeResultStatus.RESOLVED,
            selected_input_ids=selected,
            contributing_input_ids=selected,
        )

    if node.kind is EvaluationNodeKind.CONSTANT:
        assert node.constant is not None
        return EvaluationNodeResult(
            node_id=node.node_id,
            status=NodeResultStatus.RESOLVED,
            number=_typed_to_number(node.constant),
        )

    if node.kind in {EvaluationNodeKind.SUM, EvaluationNodeKind.COUNT}:
        parent = dependencies[0]
        selected = parent.selected_input_ids
        if node.kind is EvaluationNodeKind.COUNT:
            return EvaluationNodeResult(
                node_id=node.node_id,
                status=NodeResultStatus.RESOLVED,
                number=EvaluationNumber(
                    quantity_type=QuantityType.COUNT,
                    value=Decimal(len(selected)),
                ),
                contributing_input_ids=parent.contributing_input_ids,
            )

        if not selected:
            return EvaluationNodeResult(
                node_id=node.node_id,
                status=NodeResultStatus.RESOLVED,
                number=EvaluationNumber(
                    quantity_type=QuantityType.MONEY,
                    value=Decimal("0"),
                    zero_currency_identity=True,
                ),
                contributing_input_ids=parent.contributing_input_ids,
            )
        currencies = {inputs_by_id[input_id].currency for input_id in selected}
        if len(currencies) != 1:
            issue = _issue(
                "MIXED_CURRENCY_NO_TRUSTED_CONVERSION",
                EvaluationIssueSeverity.UNRESOLVED,
                "SUM contains multiple currencies without trusted conversion",
                node_id=node.node_id,
                input_ids=selected,
            )
            return EvaluationNodeResult(
                node_id=node.node_id,
                status=NodeResultStatus.UNRESOLVED,
                contributing_input_ids=parent.contributing_input_ids,
                issues=(issue,),
            )
        currency = next(iter(currencies))
        total = sum(
            (inputs_by_id[input_id].amount for input_id in selected),
            Decimal("0"),
        )
        return EvaluationNodeResult(
            node_id=node.node_id,
            status=NodeResultStatus.RESOLVED,
            number=EvaluationNumber(
                quantity_type=QuantityType.MONEY,
                value=total,
                currency=currency,
            ),
            contributing_input_ids=parent.contributing_input_ids,
        )

    numbers = tuple(
        dependency.number for dependency in dependencies if dependency.number is not None
    )
    if len(numbers) != len(dependencies):
        issue = _issue(
            "DEPENDENCY_VALUE_MISSING",
            EvaluationIssueSeverity.ERROR,
            "numeric dependency resolved without a numeric value",
            node_id=node.node_id,
        )
        return EvaluationNodeResult(
            node_id=node.node_id,
            status=NodeResultStatus.ERROR,
            contributing_input_ids=_combine_input_ids(
                *(item.contributing_input_ids for item in dependencies)
            ),
            issues=(issue,),
        )

    contributing = _combine_input_ids(*(item.contributing_input_ids for item in dependencies))

    if node.kind in {EvaluationNodeKind.MIN, EvaluationNodeKind.MAX}:
        assert numbers
        currency, currency_issue = _money_currency(numbers, node_id=node.node_id)
        if currency_issue is not None:
            return EvaluationNodeResult(
                node_id=node.node_id,
                status=NodeResultStatus.UNRESOLVED,
                contributing_input_ids=contributing,
                issues=(currency_issue,),
            )
        chosen = (
            min(numbers, key=lambda item: item.value)
            if node.kind is EvaluationNodeKind.MIN
            else max(numbers, key=lambda item: item.value)
        )
        if chosen.quantity_type is QuantityType.MONEY and chosen.currency is None:
            chosen = chosen.model_copy(
                update={
                    "currency": currency,
                    "zero_currency_identity": currency is None,
                }
            )
        return EvaluationNodeResult(
            node_id=node.node_id,
            status=NodeResultStatus.RESOLVED,
            number=chosen,
            contributing_input_ids=contributing,
        )

    left, right = numbers
    if node.kind in {EvaluationNodeKind.ADD, EvaluationNodeKind.SUBTRACT}:
        currency, currency_issue = _money_currency(numbers, node_id=node.node_id)
        if currency_issue is not None:
            return EvaluationNodeResult(
                node_id=node.node_id,
                status=NodeResultStatus.UNRESOLVED,
                contributing_input_ids=contributing,
                issues=(currency_issue,),
            )
        value = (
            left.value + right.value
            if node.kind is EvaluationNodeKind.ADD
            else left.value - right.value
        )
        qtype = node.quantity_type
        assert qtype is not None
        return EvaluationNodeResult(
            node_id=node.node_id,
            status=NodeResultStatus.RESOLVED,
            number=EvaluationNumber(
                quantity_type=qtype,
                value=value,
                currency=currency if qtype is QuantityType.MONEY else None,
                zero_currency_identity=(
                    qtype is QuantityType.MONEY and currency is None and value == 0
                ),
            ),
            contributing_input_ids=contributing,
        )

    if node.kind is EvaluationNodeKind.MULTIPLY:
        qtype = node.quantity_type
        assert qtype is not None
        currency, currency_issue = _money_currency(numbers, node_id=node.node_id)
        if currency_issue is not None:
            return EvaluationNodeResult(
                node_id=node.node_id,
                status=NodeResultStatus.UNRESOLVED,
                contributing_input_ids=contributing,
                issues=(currency_issue,),
            )
        value = left.value * right.value
        return EvaluationNodeResult(
            node_id=node.node_id,
            status=NodeResultStatus.RESOLVED,
            number=EvaluationNumber(
                quantity_type=qtype,
                value=value,
                currency=currency if qtype is QuantityType.MONEY else None,
                zero_currency_identity=(
                    qtype is QuantityType.MONEY and currency is None and value == 0
                ),
            ),
            contributing_input_ids=contributing,
        )

    if node.kind is EvaluationNodeKind.DIVIDE:
        if right.value == 0:
            issue = _issue(
                "ZERO_DENOMINATOR",
                EvaluationIssueSeverity.ERROR,
                "division denominator is zero",
                node_id=node.node_id,
            )
            return EvaluationNodeResult(
                node_id=node.node_id,
                status=NodeResultStatus.ERROR,
                contributing_input_ids=contributing,
                issues=(issue,),
            )
        issues: list[EvaluationIssue] = []
        if right.value < 0:
            issues.append(
                _issue(
                    "NEGATIVE_DENOMINATOR",
                    EvaluationIssueSeverity.WARNING,
                    "division denominator is negative; formula was evaluated faithfully",
                    node_id=node.node_id,
                )
            )
        if left.quantity_type is QuantityType.MONEY and right.quantity_type is QuantityType.MONEY:
            _currency, currency_issue = _money_currency(numbers, node_id=node.node_id)
            if currency_issue is not None:
                return EvaluationNodeResult(
                    node_id=node.node_id,
                    status=NodeResultStatus.UNRESOLVED,
                    contributing_input_ids=contributing,
                    issues=(currency_issue,),
                )
        return EvaluationNodeResult(
            node_id=node.node_id,
            status=NodeResultStatus.RESOLVED,
            number=EvaluationNumber(
                quantity_type=QuantityType.RATIO,
                value=left.value / right.value,
            ),
            contributing_input_ids=contributing,
            issues=tuple(issues),
        )

    issue = _issue(
        "UNSUPPORTED_NODE_KIND",
        EvaluationIssueSeverity.ERROR,
        f"executor does not support node kind {node.kind.value}",
        node_id=node.node_id,
    )
    return EvaluationNodeResult(
        node_id=node.node_id,
        status=NodeResultStatus.ERROR,
        contributing_input_ids=contributing,
        issues=(issue,),
    )
