"""Covenant expression AST and type inference (Stage 5D)."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from halyk_agent.domain.common import NonEmptyStr
from halyk_agent.domain.covenants.quantity import CovenantTypeError, QuantityType, TypedQuantity


class MetricCategory(StrEnum):
    """
    Neutral selector categories for later Stage 5F classification.

    Stage 5D does not classify ledger rows; it only declares needed universes.
    """

    REVENUE = "REVENUE"
    CAPEX = "CAPEX"
    OPEX = "OPEX"
    INTEREST_EXPENSE = "INTEREST_EXPENSE"
    LEASE_PAYMENTS = "LEASE_PAYMENTS"
    RELATED_PARTY_PAYMENTS = "RELATED_PARTY_PAYMENTS"
    LABOR = "LABOR"
    UTILITIES = "UTILITIES"
    TAXES = "TAXES"
    INSURANCE_PREMIUMS = "INSURANCE_PREMIUMS"
    RENT = "RENT"
    FINANCING_INFLOWS = "FINANCING_INFLOWS"
    SEVERANCE_LIABILITY = "SEVERANCE_LIABILITY"
    CAPITAL_ASSET_TRANSFER = "CAPITAL_ASSET_TRANSFER"
    CAPITAL_ASSET_TRANSFERS_TO_UNRESTRICTED_SUBS = "CAPITAL_ASSET_TRANSFERS_TO_UNRESTRICTED_SUBS"
    GROUP_CAPEX = "GROUP_CAPEX"
    ONE_TIME_ADD_BACKS = "ONE_TIME_ADD_BACKS"
    # Non-selector audit categories: classified for provenance / related-party
    # overlays, but never widened into explicit covenant statement-line selectors.
    OTHER_EXPENSE = "OTHER_EXPENSE"
    NON_OPERATING_INCOME = "NON_OPERATING_INCOME"


class TransactionSelector(BaseModel):
    """Declares the category/flag universe a later calculation must satisfy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    category: MetricCategory
    include_flags: tuple[NonEmptyStr, ...] = ()
    exclude_flags: tuple[NonEmptyStr, ...] = ()
    related_party_only: bool = False
    group_level: bool = False


class Constant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["constant"] = "constant"
    quantity: TypedQuantity


class TransactionSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["transaction_set"] = "transaction_set"
    selector: TransactionSelector


class Sum(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["sum"] = "sum"
    of: Expr


class Min(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["min"] = "min"
    args: tuple[Expr, ...] = Field(min_length=2)


class Max(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["max"] = "max"
    args: tuple[Expr, ...] = Field(min_length=2)


class Count(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["count"] = "count"
    of: Expr


class Add(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["add"] = "add"
    left: Expr
    right: Expr


class Subtract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["subtract"] = "subtract"
    left: Expr
    right: Expr


class Multiply(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["multiply"] = "multiply"
    left: Expr
    right: Expr


class Divide(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["divide"] = "divide"
    numerator: Expr
    denominator: Expr


Expr = Annotated[
    Constant | TransactionSet | Sum | Min | Max | Count | Add | Subtract | Multiply | Divide,
    Field(discriminator="kind"),
]

EXPR_ADAPTER: TypeAdapter[Expr] = TypeAdapter(Expr)


def _tx_sum(category: MetricCategory, **kwargs: object) -> Sum:
    return Sum(
        of=TransactionSet(
            selector=TransactionSelector(category=category, **kwargs)  # type: ignore[arg-type]
        )
    )


def money_sum(category: MetricCategory, **kwargs: object) -> Sum:
    return _tx_sum(category, **kwargs)


def infer_quantity_type(expr: Expr) -> QuantityType:
    """Infer output QuantityType; raise CovenantTypeError on illegal combinations."""
    if isinstance(expr, Constant):
        return expr.quantity.quantity_type
    if isinstance(expr, TransactionSet):
        return QuantityType.MONEY
    if isinstance(expr, Sum):
        if isinstance(expr.of, TransactionSet):
            return QuantityType.MONEY
        inner = infer_quantity_type(expr.of)
        if inner is QuantityType.MONEY:
            return QuantityType.MONEY
        raise CovenantTypeError(f"SUM requires MONEY operand, got {inner.value}")
    if isinstance(expr, Count):
        return QuantityType.COUNT
    if isinstance(expr, (Min, Max)):
        types = {infer_quantity_type(arg) for arg in expr.args}
        if len(types) != 1:
            type_names = sorted(item.value for item in types)
            raise CovenantTypeError(
                f"{expr.kind.upper()} requires homogeneous types, got {type_names}"
            )
        return next(iter(types))
    if isinstance(expr, Add):
        left = infer_quantity_type(expr.left)
        right = infer_quantity_type(expr.right)
        if left is right and left in {QuantityType.MONEY, QuantityType.RATIO, QuantityType.COUNT}:
            return left
        raise CovenantTypeError(f"illegal Add({left.value}, {right.value})")
    if isinstance(expr, Subtract):
        left = infer_quantity_type(expr.left)
        right = infer_quantity_type(expr.right)
        if left is right and left in {QuantityType.MONEY, QuantityType.RATIO, QuantityType.COUNT}:
            return left
        raise CovenantTypeError(f"illegal Subtract({left.value}, {right.value})")
    if isinstance(expr, Multiply):
        left = infer_quantity_type(expr.left)
        right = infer_quantity_type(expr.right)
        if left is QuantityType.RATIO and right is QuantityType.MONEY:
            return QuantityType.MONEY
        if left is QuantityType.MONEY and right is QuantityType.RATIO:
            return QuantityType.MONEY
        if left is QuantityType.RATIO and right is QuantityType.RATIO:
            return QuantityType.RATIO
        raise CovenantTypeError(f"illegal Multiply({left.value}, {right.value})")
    if isinstance(expr, Divide):
        num = infer_quantity_type(expr.numerator)
        den = infer_quantity_type(expr.denominator)
        if num is QuantityType.MONEY and den is QuantityType.MONEY:
            return QuantityType.RATIO
        if num is QuantityType.RATIO and den is QuantityType.RATIO:
            return QuantityType.RATIO
        raise CovenantTypeError(f"illegal Divide({num.value}, {den.value})")
    raise CovenantTypeError(f"unknown expression kind: {type(expr)!r}")


# Rebuild forward refs for Pydantic.
Sum.model_rebuild()
Min.model_rebuild()
Max.model_rebuild()
Count.model_rebuild()
Add.model_rebuild()
Subtract.model_rebuild()
Multiply.model_rebuild()
Divide.model_rebuild()
