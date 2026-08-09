"""Covenant expression AST and type inference (Stage 5D)."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from halyk_agent.domain.common import NonEmptyStr
from halyk_agent.domain.covenants.quantity import CovenantTypeError, QuantityType, TypedQuantity


class MetricCategory(StrEnum):
    """
    Neutral selector categories for later Stage 5F classification.

    Stage 5D does not classify ledger rows; it only declares needed universes.
    A category names *what* is measured; ``AccountingScope`` names *whose* books
    it is measured on. The two together form metric identity.
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
    # Private-corpus statement lines. Declared here so a covenant can *request*
    # the universe even before Stage 5E/5F can supply it — never substituted
    # with a merely adjacent existing category.
    MARKETING = "MARKETING"
    CONSULTING_SERVICES = "CONSULTING_SERVICES"
    SCHEDULED_PRINCIPAL_REPAYMENT = "SCHEDULED_PRINCIPAL_REPAYMENT"
    FINANCIAL_DEBT = "FINANCIAL_DEBT"
    GUARANTEE_LIABILITY = "GUARANTEE_LIABILITY"
    INDEMNITY_LIABILITY = "INDEMNITY_LIABILITY"
    RESTRICTED_PAYMENTS = "RESTRICTED_PAYMENTS"
    ASSET_DISPOSALS = "ASSET_DISPOSALS"
    # Non-selector audit categories: classified for provenance / related-party
    # overlays, but never widened into explicit covenant statement-line selectors.
    OTHER_EXPENSE = "OTHER_EXPENSE"
    NON_OPERATING_INCOME = "NON_OPERATING_INCOME"


class AccountingScope(StrEnum):
    """Whose books a metric is measured on. Part of metric identity."""

    BORROWER = "BORROWER"
    GROUP = "GROUP"
    PARENT = "PARENT"
    SUBSIDIARY = "SUBSIDIARY"
    UNRESTRICTED_SUBSIDIARY = "UNRESTRICTED_SUBSIDIARY"


class PeriodGrouping(StrEnum):
    """Sub-period partition used by period-aware aggregation.

    ``FULL_PERIOD`` is the single-part partition: the whole measurement period as
    one bucket. It exists so a covenant can state a period basis without also
    claiming a sub-period quantifier.
    """

    FULL_PERIOD = "FULL_PERIOD"
    FINANCIAL_QUARTER = "FINANCIAL_QUARTER"
    FINANCIAL_MONTH = "FINANCIAL_MONTH"
    FINANCIAL_YEAR = "FINANCIAL_YEAR"


class PeriodReducer(StrEnum):
    """How per-sub-period values collapse into one reported number."""

    MIN = "MIN"
    MAX = "MAX"
    SUM = "SUM"


class PeriodBasis(StrEnum):
    """Which date assigns a transaction to a sub-period."""

    CASH_DATE = "CASH_DATE"
    ACCOUNTING_RECOGNITION = "ACCOUNTING_RECOGNITION"


class Comparator(StrEnum):
    LT = "LT"
    LTE = "LTE"
    GT = "GT"
    GTE = "GTE"
    EQ = "EQ"


class TransactionSelector(BaseModel):
    """Declares the category/flag universe a later calculation must satisfy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    category: MetricCategory
    include_flags: tuple[NonEmptyStr, ...] = ()
    exclude_flags: tuple[NonEmptyStr, ...] = ()
    related_party_only: bool = False
    group_level: bool = False
    scope: AccountingScope = AccountingScope.BORROWER

    @model_validator(mode="before")
    @classmethod
    def _scope_from_legacy_flag(cls, data: Any) -> Any:
        # ``group_level`` predates ``scope``. A legacy selector that only sets the
        # flag still means GROUP, so normalize rather than reject — the two must
        # never describe different metrics.
        if isinstance(data, dict) and data.get("group_level") and "scope" not in data:
            return {**data, "scope": AccountingScope.GROUP}
        return data

    @model_validator(mode="after")
    def _scope_agrees_with_legacy_flag(self) -> TransactionSelector:
        if self.group_level and self.scope is AccountingScope.BORROWER:
            raise ValueError("group_level selector must declare a non-BORROWER scope")
        return self


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


class PeriodAggregate(BaseModel):
    """Partition the measurement period, evaluate ``of`` per part, then reduce.

    ``MAX`` over quarters expresses "in every quarter X must hold" and reports the
    worst quarter; ``MIN`` expresses the same for a floor covenant. A quantifier
    over sub-periods therefore needs no separate boolean node.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["period_aggregate"] = "period_aggregate"
    of: Expr
    grouping: PeriodGrouping = PeriodGrouping.FINANCIAL_QUARTER
    reducer: PeriodReducer = PeriodReducer.MAX
    basis: PeriodBasis = PeriodBasis.CASH_DATE


Expr = Annotated[
    Constant
    | TransactionSet
    | Sum
    | Min
    | Max
    | Count
    | Add
    | Subtract
    | Multiply
    | Divide
    | PeriodAggregate,
    Field(discriminator="kind"),
]

EXPR_ADAPTER: TypeAdapter[Expr] = TypeAdapter(Expr)


class Compare(BaseModel):
    """Typed comparison of two expressions.

    Both sides are expressions, so a threshold may itself be computed (for
    example "5% of Group CAPEX"). A covenant is not required to compare against
    a literal constant.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["compare"] = "compare"
    left: Expr
    comparator: Comparator
    right: Expr


class And(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["and"] = "and"
    args: tuple[BoolExpr, ...] = Field(min_length=2)


class Or(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["or"] = "or"
    args: tuple[BoolExpr, ...] = Field(min_length=2)


class Not(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["not"] = "not"
    of: BoolExpr


class Always(BaseModel):
    """Unconditional truth — the activation predicate of a normal covenant."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["always"] = "always"


BoolExpr = Annotated[
    Compare | And | Or | Not | Always,
    Field(discriminator="kind"),
]

BOOL_EXPR_ADAPTER: TypeAdapter[BoolExpr] = TypeAdapter(BoolExpr)


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
    if isinstance(expr, PeriodAggregate):
        # Reducing per-period values preserves the operand's quantity type.
        return infer_quantity_type(expr.of)
    raise CovenantTypeError(f"unknown expression kind: {type(expr)!r}")


def _comparable(left: QuantityType, right: QuantityType) -> bool:
    normalized = {
        QuantityType.PERCENT: QuantityType.RATIO,
    }
    return normalized.get(left, left) is normalized.get(right, right)


def validate_bool_expr(expr: BoolExpr) -> None:
    """Type-check every comparison inside a boolean predicate."""
    if isinstance(expr, Always):
        return
    if isinstance(expr, Not):
        validate_bool_expr(expr.of)
        return
    if isinstance(expr, (And, Or)):
        for arg in expr.args:
            validate_bool_expr(arg)
        return
    if isinstance(expr, Compare):
        left = infer_quantity_type(expr.left)
        right = infer_quantity_type(expr.right)
        if not _comparable(left, right):
            raise CovenantTypeError(
                f"illegal Compare({left.value} {expr.comparator.value} {right.value})"
            )
        return
    raise CovenantTypeError(f"unknown boolean expression kind: {type(expr)!r}")


def iter_expressions(expr: BoolExpr) -> tuple[Expr, ...]:
    """Return every numeric expression referenced by a boolean predicate."""
    found: list[Expr] = []

    def walk(node: BoolExpr) -> None:
        if isinstance(node, Compare):
            found.extend((node.left, node.right))
        elif isinstance(node, Not):
            walk(node.of)
        elif isinstance(node, (And, Or)):
            for arg in node.args:
                walk(arg)

    walk(expr)
    return tuple(found)


# Rebuild forward refs for Pydantic.
Sum.model_rebuild()
Min.model_rebuild()
Max.model_rebuild()
Count.model_rebuild()
Add.model_rebuild()
Subtract.model_rebuild()
Multiply.model_rebuild()
Divide.model_rebuild()
PeriodAggregate.model_rebuild()
Compare.model_rebuild()
And.model_rebuild()
Or.model_rebuild()
Not.model_rebuild()
