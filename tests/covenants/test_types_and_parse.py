"""Unit tests for Stage 5D covenant types, AST, renderer, and parsers."""

# ruff: noqa: RUF001

from __future__ import annotations

from decimal import Decimal

import pytest

from halyk_agent.domain.covenants.ast import (
    Add,
    Divide,
    MetricCategory,
    infer_quantity_type,
    money_sum,
)
from halyk_agent.domain.covenants.formulas import match_formula
from halyk_agent.domain.covenants.models import Comparator, ScopeKind, ScopeProvenance
from halyk_agent.domain.covenants.parse import (
    parse_comparator,
    parse_threshold,
    related_party_phrase,
    resolve_scope,
)
from halyk_agent.domain.covenants.quantity import CovenantTypeError, QuantityType, TypedQuantity
from halyk_agent.domain.covenants.render import render_expr, render_quantity


def test_float_threshold_rejected() -> None:
    with pytest.raises(TypeError):
        TypedQuantity(quantity_type=QuantityType.RATIO, value=0.42)  # type: ignore[arg-type]


def test_money_requires_currency_ratio_forbids_currency() -> None:
    money = TypedQuantity(quantity_type=QuantityType.MONEY, value=Decimal("100.00"), currency="USD")
    assert money.currency == "USD"
    with pytest.raises(ValueError):
        TypedQuantity(quantity_type=QuantityType.RATIO, value=Decimal("0.42"), currency="USD")


def test_money_over_money_is_ratio() -> None:
    expr = Divide(
        numerator=money_sum(MetricCategory.CAPEX),
        denominator=money_sum(MetricCategory.REVENUE),
    )
    assert infer_quantity_type(expr) is QuantityType.RATIO


def test_money_plus_ratio_invalid() -> None:
    expr = Add(
        left=money_sum(MetricCategory.REVENUE),
        right=Divide(
            numerator=money_sum(MetricCategory.CAPEX),
            denominator=money_sum(MetricCategory.REVENUE),
        ),
    )
    with pytest.raises(CovenantTypeError):
        infer_quantity_type(expr)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("обязуется не допускать, чтобы показатель превышал 0.42x", Comparator.LTE),
        ("на уровне не менее $7,100,000.00", Comparator.GTE),
        ("не должны превышать $500,000.00", Comparator.LTE),
        ("must not exceed 0.05x", Comparator.LTE),
        ("not less than 1.20x", Comparator.GTE),
        ("at least $1,000.00", Comparator.GTE),
        ("at most $2,000.00", Comparator.LTE),
    ],
)
def test_comparator_phrases(text: str, expected: Comparator) -> None:
    parsed = parse_comparator(text)
    assert parsed is not None
    assert parsed.comparator is expected


@pytest.mark.parametrize(
    ("text", "qtype", "value", "currency"),
    [
        ("превышал 0.42x", QuantityType.RATIO, Decimal("0.42"), None),
        ("не менее $7,100,000.00", QuantityType.MONEY, Decimal("7100000.00"), "USD"),
        ("limit 12.5%", QuantityType.PERCENT, Decimal("12.5"), None),
        ("USD 1,000.00 ceiling", QuantityType.MONEY, Decimal("1000.00"), "USD"),
        ("EUR 1,000.00 ceiling", QuantityType.MONEY, Decimal("1000.00"), "EUR"),
        ("€1,000.00 ceiling", QuantityType.MONEY, Decimal("1000.00"), "EUR"),
        ("₸1,000.00 ceiling", QuantityType.MONEY, Decimal("1000.00"), "KZT"),
    ],
)
def test_threshold_parsing(
    text: str, qtype: QuantityType, value: Decimal, currency: str | None
) -> None:
    parsed = parse_threshold(text)
    assert parsed.status == "ok"
    assert parsed.quantity is not None
    assert parsed.quantity.quantity_type is qtype
    assert parsed.quantity.value == value
    assert parsed.quantity.currency == currency


def test_formula_capital_intensity() -> None:
    text = (
        "Пункт 6.1 Maximum Capital Intensity Ratio. Заёмщик обязуется не допускать, "
        "чтобы коэффициент капиталоёмкости за период с 2025-01-01 по 2025-12-31 "
        "превышал 0.42x. Коэффициент капиталоёмкости означает отношение совокупных "
        "капитальных затрат за период к сумме операционных расходов и арендных платежей."
    )
    match = match_formula(text)
    assert match is not None
    assert match.family_id == "CAPITAL_INTENSITY_RATIO"
    assert infer_quantity_type(match.metric) is QuantityType.RATIO
    assert "CAPEX" in render_expr(match.metric)


def test_formula_min_revenue() -> None:
    text = (
        "Пункт 6.2 Минимальная выручка по категории. Заёмщик обязуется поддерживать "
        "совокупный объём поступлений по статье Выручка за период с 2025-01-01 по "
        "2025-12-31 на уровне не менее $7,100,000.00."
    )
    match = match_formula(text)
    assert match is not None
    assert match.family_id == "MIN_REVENUE"
    assert infer_quantity_type(match.metric) is QuantityType.MONEY


def test_render_quantity_ratio_not_money() -> None:
    q = TypedQuantity(quantity_type=QuantityType.RATIO, value=Decimal("0.42"))
    rendered = render_quantity(q)
    assert "0.42x" in rendered
    assert "USD" not in rendered


def test_related_expenses_not_related_party_scope() -> None:
    text = "выплаты персоналу и связанные с ними расходы за период"
    assert related_party_phrase(text) is None
    scope = resolve_scope(text, selectors=())
    assert scope.scope_kind is ScopeKind.BORROWER
    assert scope.provenance is ScopeProvenance.DEFAULT_BORROWER_BY_RULE


def test_related_party_phrase_positive() -> None:
    assert related_party_phrase("платежи связанным сторонам не должны превышать")
    assert related_party_phrase("payments to related parties must not exceed")
    assert related_party_phrase("related expenses") is None
