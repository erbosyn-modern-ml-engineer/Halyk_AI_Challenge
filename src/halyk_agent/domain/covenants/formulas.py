"""Deterministic formula-family matchers for public/private covenant shapes."""

from __future__ import annotations

import re
from dataclasses import dataclass

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
    Subtract,
    Sum,
    TransactionSelector,
    TransactionSet,
    money_sum,
)
from halyk_agent.domain.covenants.models import ActivationCondition, Comparator
from halyk_agent.domain.covenants.parse import parse_all_money
from halyk_agent.domain.covenants.quantity import TypedQuantity


@dataclass(frozen=True, slots=True)
class FormulaMatch:
    family_id: str
    metric: Expr
    activation_condition: ActivationCondition | None = None
    threshold_override: TypedQuantity | None = None
    comparator_override: Comparator | None = None


def _norm(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


def _ebitda() -> Subtract:
    return Subtract(left=money_sum(MetricCategory.REVENUE), right=money_sum(MetricCategory.OPEX))


def _adj_ebitda() -> Add:
    return Add(left=_ebitda(), right=money_sum(MetricCategory.ONE_TIME_ADD_BACKS))


def match_formula(clause_text: str) -> FormulaMatch | None:
    """Match a known formula family using legal-phrase patterns (specific → generic)."""
    text = _norm(clause_text)
    low = text.casefold()

    if ("покрытия процентов" in low or "interest cover" in low) and (
        "ebitda" in low or "выручка за вычетом операционных" in low
    ):
        return FormulaMatch(
            family_id="INTEREST_COVER_RATIO",
            metric=Divide(
                numerator=_ebitda(),
                denominator=money_sum(MetricCategory.INTEREST_EXPENSE),
            ),
            comparator_override=Comparator.GTE,
        )

    if "springing" in low or (
        "поступлений по финансированию" in low and "ebitda" in low and "только при условии" in low
    ):
        moneys = parse_all_money(text)
        activation = None
        if moneys:
            activation = ActivationCondition(
                metric=money_sum(MetricCategory.FINANCING_INFLOWS),
                comparator=Comparator.GT,
                threshold=moneys[0][0],
            )
        return FormulaMatch(
            family_id="SPRINGING_DRAWDOWN_LEVERAGE",
            metric=Divide(
                numerator=money_sum(MetricCategory.FINANCING_INFLOWS),
                denominator=_ebitda(),
            ),
            activation_condition=activation,
            comparator_override=Comparator.LTE,
        )

    if "cover of applications by sources" in low or (
        "выручки и поступлений по финансированию" in low and "операционных и капитальных" in low
    ):
        return FormulaMatch(
            family_id="SOURCES_OVER_USES_RATIO",
            metric=Divide(
                numerator=Add(
                    left=money_sum(MetricCategory.REVENUE),
                    right=money_sum(MetricCategory.FINANCING_INFLOWS),
                ),
                denominator=Add(
                    left=money_sum(MetricCategory.OPEX),
                    right=money_sum(MetricCategory.CAPEX),
                ),
            ),
            comparator_override=Comparator.GTE,
        )

    if "capital intensity" in low or "капиталоёмкости" in low or "капиталоемкости" in low:
        return FormulaMatch(
            family_id="CAPITAL_INTENSITY_RATIO",
            metric=Divide(
                numerator=money_sum(MetricCategory.CAPEX),
                denominator=Add(
                    left=money_sum(MetricCategory.OPEX),
                    right=money_sum(MetricCategory.LEASE_PAYMENTS),
                ),
            ),
            comparator_override=Comparator.LTE,
        )

    if "страхово" in low and ("арендн" in low or "коммунальн" in low):
        return FormulaMatch(
            family_id="MIN_INSURANCE_COVER_RATIO",
            metric=Divide(
                numerator=money_sum(MetricCategory.INSURANCE_PREMIUMS),
                denominator=Add(
                    left=money_sum(MetricCategory.RENT),
                    right=money_sum(MetricCategory.UTILITIES),
                ),
            ),
            comparator_override=Comparator.GTE,
        )

    if "скорректированн" in low and "ebitda" in low and "выруч" in low:
        return FormulaMatch(
            family_id="MIN_ADJ_EBITDA_MARGIN",
            metric=Divide(numerator=_adj_ebitda(), denominator=money_sum(MetricCategory.REVENUE)),
            comparator_override=Comparator.GTE,
        )

    if "капитальных затрат группы" in low or (
        "групп" in low and "капитальн" in low and "ebitda" in low and "отношен" in low
    ):
        return FormulaMatch(
            family_id="MAX_GROUP_CAPEX_TO_BORROWER_EBITDA",
            metric=Divide(
                numerator=money_sum(MetricCategory.GROUP_CAPEX, group_level=True),
                denominator=_ebitda(),
            ),
            comparator_override=Comparator.LTE,
        )

    if "налогов" in low and "коммунальн" in low and "ebitda" in low:
        return FormulaMatch(
            family_id="MAX_TAX_UTILITIES_TO_EBITDA",
            metric=Divide(
                numerator=Add(
                    left=money_sum(MetricCategory.TAXES),
                    right=money_sum(MetricCategory.UTILITIES),
                ),
                denominator=_ebitda(),
            ),
            comparator_override=Comparator.LTE,
        )

    if (
        "связанным сторонам" in low
        and "операционных расходов" in low
        and re.search(r"\d+(?:\.\d+)?\s*x", low)
    ):
        return FormulaMatch(
            family_id="MAX_RP_AS_RATIO_OF_OPEX",
            metric=Divide(
                numerator=money_sum(MetricCategory.RELATED_PARTY_PAYMENTS, related_party_only=True),
                denominator=money_sum(MetricCategory.OPEX),
            ),
            comparator_override=Comparator.LTE,
        )

    if (
        "оплату труда" in low
        and "коммунальн" in low
        and "выруч" in low
        and "не менее" in low
        and "за вычетом" not in low
    ):
        return FormulaMatch(
            family_id="MIN_REVENUE_COVER_OF_PAYROLL_UTILITIES",
            metric=Divide(
                numerator=money_sum(MetricCategory.REVENUE),
                denominator=Add(
                    left=money_sum(MetricCategory.LABOR),
                    right=money_sum(MetricCategory.UTILITIES),
                ),
            ),
            comparator_override=Comparator.GTE,
        )

    if "неограниченн" in low and "дочерн" in low and "капитальн" in low:
        return FormulaMatch(
            family_id="MAX_CAPITAL_TRANSFERS_TO_UNRESTRICTED_SUBS_RATIO",
            metric=Divide(
                numerator=money_sum(MetricCategory.CAPITAL_ASSET_TRANSFERS_TO_UNRESTRICTED_SUBS),
                denominator=money_sum(MetricCategory.CAPEX),
            ),
            comparator_override=Comparator.LTE,
        )

    if (
        ("related-party" in low or "аффилир" in low or "связанн" in low)
        and ("от выручки" in low or "proportion of revenue" in low)
        and re.search(r"\d+(?:\.\d+)?\s*x", low)
    ):
        return FormulaMatch(
            family_id="MAX_RELATED_PARTY_RATIO_OF_REVENUE",
            metric=Divide(
                numerator=money_sum(MetricCategory.RELATED_PARTY_PAYMENTS, related_party_only=True),
                denominator=money_sum(MetricCategory.REVENUE),
            ),
            comparator_override=Comparator.LTE,
        )

    if "выручка за вычетом наибольшей" in low or (
        "наибольшей из величин" in low and "выруч" in low
    ):
        return FormulaMatch(
            family_id="MIN_REVENUE_MINUS_MAX_OVERHEAD",
            metric=Subtract(
                left=money_sum(MetricCategory.REVENUE),
                right=Max(
                    args=(
                        money_sum(MetricCategory.LABOR),
                        money_sum(MetricCategory.TAXES),
                    )
                ),
            ),
            comparator_override=Comparator.GTE,
        )

    if "отдельная статья накладных" in low or "individual overhead" in low:
        return FormulaMatch(
            family_id="MAX_SINGLE_OVERHEAD_LINE",
            metric=Max(
                args=(
                    money_sum(MetricCategory.LABOR),
                    money_sum(MetricCategory.UTILITIES),
                )
            ),
            comparator_override=Comparator.LTE,
        )

    if "обязательства по персоналу" in low or ("personnel" in low and "выходных пособий" in low):
        return FormulaMatch(
            family_id="MAX_PERSONNEL_OBLIGATIONS",
            metric=Add(
                left=money_sum(MetricCategory.LABOR),
                right=money_sum(MetricCategory.SEVERANCE_LIABILITY),
            ),
            comparator_override=Comparator.LTE,
        )

    if (
        ("связанн" in low or "аффилир" in low or "related" in low)
        and ("платеж" in low or "payment" in low)
        and "$" in text
        and "от выручки" not in low
        and "операционных расходов" not in low
    ):
        return FormulaMatch(
            family_id="MAX_RELATED_PARTY_PAYMENTS",
            metric=money_sum(MetricCategory.RELATED_PARTY_PAYMENTS, related_party_only=True),
            comparator_override=Comparator.LTE,
        )

    if (
        ("капитальн" in low or "capex" in low)
        and ("превыш" in low or "не превыша" in low or "не вправе" in low)
        and "отношен" not in low
        and "групп" not in low
        and "неограниченн" not in low
    ):
        return FormulaMatch(
            family_id="MAX_CAPEX",
            metric=money_sum(MetricCategory.CAPEX),
            comparator_override=Comparator.LTE,
        )

    if (
        ("выруч" in low or "revenue" in low)
        and ("не менее" in low or "не ниже" in low or "минимальн" in low)
        and "$" in text
        and "за вычетом наибольшей" not in low
    ):
        family = "MIN_REVENUE_QUARTER" if ("квартал" in low or "quarter" in low) else "MIN_REVENUE"
        return FormulaMatch(
            family_id=family,
            metric=money_sum(MetricCategory.REVENUE),
            comparator_override=Comparator.GTE,
        )

    return None


def collect_selectors(expr: Expr) -> tuple[TransactionSelector, ...]:
    found: list[TransactionSelector] = []

    def walk(node: Expr) -> None:
        if isinstance(node, TransactionSet):
            found.append(node.selector)
        elif isinstance(node, Constant):
            return
        elif isinstance(node, (Sum, Count)):
            walk(node.of)
        elif isinstance(node, (Min, Max)):
            for arg in node.args:
                walk(arg)
        elif isinstance(node, (Add, Subtract, Multiply)):
            walk(node.left)
            walk(node.right)
        elif isinstance(node, Divide):
            walk(node.numerator)
            walk(node.denominator)

    walk(expr)
    seen: set[str] = set()
    out: list[TransactionSelector] = []
    for selector in found:
        key = selector.model_dump_json()
        if key not in seen:
            seen.add(key)
            out.append(selector)
    return tuple(out)
