"""Deterministic clause-structure parsing into typed covenant plans (Stage 5D).

This parser is compositional rather than template-based: it recognizes independent
semantic units — statement-line metrics, accounting scope, comparison direction,
thresholds (literal or expression-valued), sub-period quantifiers and conditional
connectives — and assembles them into a CovenantPlan. Wording it does not
recognize is left to the bounded semantic planner rather than growing a new
phrase-specific family for every clause.
"""

# Multilingual covenant vocabulary is intentional.
# ruff: noqa: RUF001

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from halyk_agent.domain.covenants.ast import (
    AccountingScope,
    Add,
    Always,
    And,
    BoolExpr,
    Comparator,
    Compare,
    Constant,
    Divide,
    Expr,
    MetricCategory,
    Min,
    Multiply,
    Or,
    PeriodAggregate,
    PeriodBasis,
    PeriodGrouping,
    PeriodReducer,
    Subtract,
    Sum,
    TransactionSelector,
    TransactionSet,
)
from halyk_agent.domain.covenants.models import CovenantPlan
from halyk_agent.domain.covenants.plans import breach_comparator, finalize_plan
from halyk_agent.domain.covenants.quantity import CovenantTypeError, QuantityType, TypedQuantity


@dataclass(frozen=True, slots=True)
class StructureMatch:
    plan: CovenantPlan
    family_id: str
    # True when this structure carries meaning a legacy family match would lose.
    overrides_family: bool


def _norm(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


# --------------------------------------------------------------------------
# Statement-line lexicon. Phrases only; no scenario, borrower or file names.
# --------------------------------------------------------------------------
_CATEGORY_PATTERNS: tuple[tuple[str, MetricCategory], ...] = (
    (r"капитальн\w*\s+затрат|capital\s+expenditure|\bcapex\b", MetricCategory.CAPEX),
    (r"маркетингов\w*\s+расход|marketing\s+(?:spend|expense)", MetricCategory.MARKETING),
    (
        r"консультацион\w*|консалтингов\w*|consulting\s+(?:services?|fees?|expense)",
        MetricCategory.CONSULTING_SERVICES,
    ),
    (r"операционн\w*\s+расход|operating\s+expense|\bopex\b", MetricCategory.OPEX),
    (r"выручк\w*|\brevenue\b", MetricCategory.REVENUE),
    (r"арендн\w*\s+платеж|аренд\w*|\brent\b|lease\s+payment", MetricCategory.RENT),
    (r"коммунальн\w*|\butilit", MetricCategory.UTILITIES),
    (r"страхов\w*\s+преми|страхов\w*|insurance\s+premium", MetricCategory.INSURANCE_PREMIUMS),
    (
        r"процентн\w*\s+расход|расход\w*\s+по\s+процент|interest\s+expense",
        MetricCategory.INTEREST_EXPENSE,
    ),
    (r"оплат\w*\s+труда|фонд\w*\s+оплаты|payroll|labou?r\s+cost", MetricCategory.LABOR),
    (r"налог\w*|\btaxes?\b", MetricCategory.TAXES),
    (
        r"плановы\w*\s+погашени\w*\s+основного\s+долга|scheduled\s+principal",
        MetricCategory.SCHEDULED_PRINCIPAL_REPAYMENT,
    ),
    (r"гаранти\w*|поручительств\w*|\bguarantees?\b", MetricCategory.GUARANTEE_LIABILITY),
    (r"возмещени\w*\s+убытк|\bindemnit", MetricCategory.INDEMNITY_LIABILITY),
    (
        r"ограниченн\w*\s+платеж|restricted\s+payment|распределени\w*\s+в\s+пользу",
        MetricCategory.RESTRICTED_PAYMENTS,
    ),
    (
        r"связанн\w*\s+сторон|аффилир\w*|related[\s-]+part",
        MetricCategory.RELATED_PARTY_PAYMENTS,
    ),
    (
        r"поступлени\w*\s+(?:от|по)\s+финансировани|financing\s+(?:inflows?|proceeds)",
        MetricCategory.FINANCING_INFLOWS,
    ),
    (
        r"разовы\w*\s+(?:статьи|корректировк)|one[\s-]?time|add[\s-]?back",
        MetricCategory.ONE_TIME_ADD_BACKS,
    ),
)

_COMPILED_CATEGORIES = tuple(
    (re.compile(pattern, re.IGNORECASE), category) for pattern, category in _CATEGORY_PATTERNS
)

# Scope cues. Scope is part of metric identity, never a formatting detail.
_GROUP_RE = re.compile(
    r"(?i)групп\w*|консолидированн\w*|материнск\w*\s+компани|\bgroup\b|consolidated|ultimate\s+parent"
)
_BORROWER_RE = re.compile(r"(?i)заёмщик\w*|заемщик\w*|\bborrower\b")
_UNRESTRICTED_RE = re.compile(r"(?i)неограниченн\w*\s+дочерн|unrestricted\s+subsidiar")

# Sub-period quantifiers.
_EVERY_QUARTER_RE = re.compile(
    r"(?i)(?:кажд\w*|любо\w*|всяк\w*)\s+(?:финансов\w*\s+)?квартал"
    r"|(?:each|every|any)\s+(?:financial\s+)?quarter"
    r"|поквартальн\w*|quarterly"
)

# Accounting-recognition basis (period assignment differs from cash date).
_RECOGNITION_RE = re.compile(
    r"(?i)относится\s+к\s+тому\s+(?:финансовому\s+)?квартал"
    r"|в\s+котором\s+(?:она\s+)?призна\w*"
    r"|признан\w*\s+выручкой"
    r"|recognit|recognised|recognized\s+in\s+the\s+quarter"
)

# Conditional (springing) connectives.
_IF_THEN_RE = re.compile(
    r"(?i)\bесли\b(?P<cond>.{0,320}?)\s*,\s*то\b(?P<body>.+)"
    r"|\bif\b(?P<cond2>.{0,320}?)\s*,\s*then\b(?P<body2>.+)"
)
_UNTIL_INACTIVE_RE = re.compile(
    r"(?i)(?:пока|до\s+тех\s+пор\s+пока)\b.{0,400}?не\s+применяется"
    r"|(?:so\s+long\s+as|until)\b.{0,400}?(?:shall\s+not\s+apply|does\s+not\s+apply)"
)

# Compound breach connectives.
_BOTH_RE = re.compile(
    r"(?i)обо\w*\s+(?:из\s+)?услови|одновременн\w*|при\s+одновременном"
    r"|\bboth\b\s+.{0,60}?\band\b|only\s+if\s+.{0,80}?\band\b"
)
_EITHER_RE = re.compile(
    r"(?i)любо\w*\s+из\s+(?:следующ\w*\s+)?услови|\bлибо\b|\bили\b(?!\s+ин\w*\s+задолженност)"
    r"|\beither\b|\bor\b\s+(?:if|when)"
)

_MIN_COMPARATORS = (
    (
        re.compile(r"(?i)не\s+менее|не\s+ниже|at\s+least|not\s+less\s+than|минимальн"),
        Comparator.GTE,
    ),
    (
        re.compile(
            r"(?i)не\s+более|не\s+превыша\w*|не\s+должн\w*\s+превыша\w*|не\s+вправе"
            r"|must\s+not\s+exceed|shall\s+not\s+exceed|not\s+more\s+than|at\s+most"
            r"|не\s+допускать\s+превышени"
        ),
        Comparator.LTE,
    ),
)

_PERCENT_OF_RE = re.compile(
    r"(?i)(?P<num>\d+(?:[.,]\d+)?)\s*(?:%|процент\w*|per\s*cent|percent)\s*"
    r"(?:от\s+|of\s+|the\s+)?(?P<tail>[^.;]{0,140})"
)

_RATIO_RE = re.compile(r"(?i)(?<![0-9.,])(?P<num>\d+(?:[.,]\d+)?)\s*x\b")
_MONEY_RE = re.compile(r"\$\s*(?P<num>\d[\d,\s']*(?:\.\d{1,2})?)")


def _decimal(raw: str) -> Decimal:
    return Decimal(raw.replace(",", "").replace(" ", "").replace("'", "").replace("\xa0", ""))


def _scope_for(window: str) -> AccountingScope:
    if _UNRESTRICTED_RE.search(window):
        return AccountingScope.UNRESTRICTED_SUBSIDIARY
    if _GROUP_RE.search(window):
        return AccountingScope.GROUP
    return AccountingScope.BORROWER


def _selector(category: MetricCategory, scope: AccountingScope) -> TransactionSelector:
    return TransactionSelector(
        category=category,
        scope=scope,
        group_level=scope is not AccountingScope.BORROWER,
        related_party_only=category
        in {MetricCategory.RELATED_PARTY_PAYMENTS, MetricCategory.RESTRICTED_PAYMENTS},
    )


def _metric(category: MetricCategory, scope: AccountingScope) -> Expr:
    return Sum(of=TransactionSet(selector=_selector(category, scope)))


def _ebitda(scope: AccountingScope) -> Expr:
    return Subtract(
        left=_metric(MetricCategory.REVENUE, scope),
        right=_metric(MetricCategory.OPEX, scope),
    )


@dataclass(frozen=True, slots=True)
class _MetricHit:
    category: MetricCategory
    scope: AccountingScope
    start: int
    end: int


def find_metric_hits(text: str) -> tuple[_MetricHit, ...]:
    """Locate every statement-line mention with its locally-declared scope."""
    hits: list[_MetricHit] = []
    for pattern, category in _COMPILED_CATEGORIES:
        for match in pattern.finditer(text):
            window = text[max(0, match.start() - 90) : match.end() + 40]
            hits.append(
                _MetricHit(
                    category=category,
                    scope=_scope_for(window),
                    start=match.start(),
                    end=match.end(),
                )
            )
    hits.sort(key=lambda item: item.start)
    return tuple(hits)


def _first_category(
    hits: tuple[_MetricHit, ...], *, skip: set[MetricCategory]
) -> _MetricHit | None:
    for hit in hits:
        if hit.category not in skip:
            return hit
    return None


_LEVERAGE_TERM_RE = re.compile(
    r"(?i)коэффициент\w*\s+(?:чист\w*\s+)?долгов\w*\s+нагрузк"
    r"|(?:net\s+)?leverage\s+ratio"
    r"|долгов\w*\s+нагрузк"
)


def _leverage_expr(window: str) -> Expr | None:
    """Debt / EBITDA when the window states a leverage ratio.

    A leverage ratio is a standard defined term: agreements often name it and
    define its components in a separate clause, so the ratio must be recognized
    without requiring the word EBITDA in the same sentence.
    """
    low = window.casefold()
    named_term = _LEVERAGE_TERM_RE.search(window) is not None
    if "ebitda" not in low and not named_term:
        return None
    if not named_term and not re.search(r"(?i)совокупн\w*\s+долг|\bdebt\b", window):
        return None
    scope = _scope_for(window)
    # Agreements in this corpus define total debt as financing drawn in period;
    # honour an explicit in-clause definition when present, else financial debt.
    numerator_category = (
        MetricCategory.FINANCING_INFLOWS
        if re.search(
            r"(?i)привлечённ\w*|привлеченн\w*|поступлени\w*\s+(?:от|по)\s+финансировани"
            r"|financing\s+(?:inflows?|proceeds)|drawn",
            window,
        )
        else MetricCategory.FINANCIAL_DEBT
    )
    return Divide(numerator=_metric(numerator_category, scope), denominator=_ebitda(scope))


def _dynamic_threshold(text: str, *, after: int) -> Expr | None:
    """A threshold expressed as a percentage of another metric."""
    for match in _PERCENT_OF_RE.finditer(text, after):
        tail = match.group("tail")
        hits = find_metric_hits(tail)
        if not hits:
            continue
        ratio = _decimal(match.group("num")) / Decimal(100)
        scope = _scope_for(tail)
        return Multiply(
            left=Constant(quantity=TypedQuantity(quantity_type=QuantityType.RATIO, value=ratio)),
            right=_metric(hits[0].category, scope),
        )
    return None


def _literal_threshold(window: str) -> TypedQuantity | None:
    """Return the threshold only when the window states exactly one.

    Two distinct amounts of the same kind in one restriction are genuinely
    ambiguous — picking the first would silently choose a covenant. Fail closed
    and let the bounded planner read the sentence instead.
    """
    money_values = {_decimal(m.group("num")) for m in _MONEY_RE.finditer(window)}
    if len(money_values) > 1:
        return None
    if money_values:
        return TypedQuantity(
            quantity_type=QuantityType.MONEY,
            value=next(iter(money_values)),
            currency="USD",
        )
    ratio_values = {_decimal(m.group("num")) for m in _RATIO_RE.finditer(window)}
    if len(ratio_values) > 1:
        return None
    if ratio_values:
        return TypedQuantity(quantity_type=QuantityType.RATIO, value=next(iter(ratio_values)))
    return None


def _comparator_in(window: str) -> Comparator | None:
    best: tuple[int, Comparator] | None = None
    for pattern, comparator in _MIN_COMPARATORS:
        match = pattern.search(window)
        if match is None:
            continue
        if best is None or match.start() < best[0]:
            best = (match.start(), comparator)
    return best[1] if best else None


def _condition_from(window: str) -> BoolExpr | None:
    """Build a comparison predicate from a conditional sub-clause."""
    left = _leverage_expr(window)
    if left is None:
        hits = find_metric_hits(window)
        if not hits:
            return None
        first = hits[0]
        if first.category is MetricCategory.REVENUE and _EVERY_QUARTER_RE.search(window):
            left = PeriodAggregate(
                of=_metric(first.category, first.scope),
                grouping=PeriodGrouping.FINANCIAL_QUARTER,
                reducer=PeriodReducer.MIN,
                basis=PeriodBasis.ACCOUNTING_RECOGNITION
                if _RECOGNITION_RE.search(window)
                else PeriodBasis.CASH_DATE,
            )
        else:
            left = _metric(first.category, first.scope)
            if len(hits) >= 2 and re.search(r"(?i)отношени|доля|% of|процент\w*\s+от", window):
                left = Divide(
                    numerator=left,
                    denominator=_metric(hits[1].category, hits[1].scope),
                )

    threshold = _literal_threshold(window)
    if threshold is None:
        return None
    # Direction matters for an activation trigger. "не менее" is a floor
    # ("at least"), while a bare "менее" is a shortfall ("less than").
    if re.search(r"(?i)не\s+(?:менее|ниже)|at\s+least|not\s+less\s+than", window):
        comparator = Comparator.GTE
    elif re.search(r"(?i)\bменее\b|\bниже\b|less\s+than|below|falls?\s+short", window):
        comparator = Comparator.LT
    else:
        comparator = Comparator.GT
    return Compare(
        left=left,
        comparator=comparator,
        right=Constant(quantity=threshold),
    )


def _restriction_from(body: str) -> tuple[Expr, Comparator, Expr] | None:
    """Build (reported_actual, compliance comparator, threshold expr) from a restriction."""
    hits = find_metric_hits(body)
    if not hits:
        return None
    comparator = _comparator_in(body)
    if comparator is None:
        return None

    ratio_of = re.search(
        r"(?i)отношени\w*|\bк\b\s+выручк|ratio\s+of|as\s+a\s+(?:proportion|percentage)", body
    )
    if ratio_of and len(hits) >= 2:
        actual: Expr = Divide(
            numerator=_metric(hits[0].category, hits[0].scope),
            denominator=_metric(hits[1].category, hits[1].scope),
        )
    elif _EVERY_QUARTER_RE.search(body):
        actual = PeriodAggregate(
            of=_metric(hits[0].category, hits[0].scope),
            grouping=PeriodGrouping.FINANCIAL_QUARTER,
            reducer=PeriodReducer.MAX if comparator is Comparator.LTE else PeriodReducer.MIN,
            basis=PeriodBasis.ACCOUNTING_RECOGNITION
            if _RECOGNITION_RE.search(body)
            else PeriodBasis.CASH_DATE,
        )
    else:
        actual = _metric(hits[0].category, hits[0].scope)

    literal = _literal_threshold(body)
    if literal is not None:
        return actual, comparator, Constant(quantity=literal)
    dynamic = _dynamic_threshold(body, after=0)
    if dynamic is not None:
        return actual, comparator, dynamic
    return None


def _springing_plan(text: str) -> StructureMatch | None:
    """IF <financial condition> THEN <restriction> ELSE inactive."""
    match = _IF_THEN_RE.search(text)
    if match is None:
        return None
    condition_text = match.group("cond") or match.group("cond2") or ""
    body_text = match.group("body") or match.group("body2") or ""
    if not condition_text or not body_text:
        return None
    activation = _condition_from(condition_text)
    if activation is None:
        return None
    restriction = _restriction_from(body_text)
    if restriction is None:
        return None
    actual, comparator, threshold = restriction
    plan = CovenantPlan(
        reported_actual=actual,
        reported_actual_quantity_type=QuantityType.MONEY,
        activation_condition=activation,
        breach_condition=Compare(
            left=actual, comparator=breach_comparator(comparator), right=threshold
        ),
    )
    return StructureMatch(
        plan=finalize_plan(plan), family_id="SPRINGING_CONDITIONAL", overrides_family=True
    )


def _compound_plan(text: str) -> StructureMatch | None:
    """Breach requires BOTH / EITHER of two financial conditions."""
    conjunctive = _BOTH_RE.search(text) is not None
    disjunctive = _EITHER_RE.search(text) is not None
    if not conjunctive and not disjunctive:
        return None
    # Split on the enumeration of conditions and require two parsable comparisons.
    parts = re.split(r"(?i)\s*(?:;|\bи\b|\bлибо\b|\bили\b|\band\b|\bor\b)\s*", text)
    conditions: list[Compare] = []
    for part in parts:
        predicate = _condition_from(part)
        if isinstance(predicate, Compare):
            conditions.append(predicate)
    if len(conditions) < 2:
        return None
    breach: BoolExpr = (
        And(args=tuple(conditions[:2])) if conjunctive else Or(args=tuple(conditions[:2]))
    )
    # The reported actual is the covenant's headline metric — the first condition's
    # left side unless the clause names a different reporting metric.
    reported = conditions[0].left
    plan = CovenantPlan(
        reported_actual=reported,
        reported_actual_quantity_type=QuantityType.RATIO,
        activation_condition=Always(),
        breach_condition=breach,
    )
    return StructureMatch(
        plan=finalize_plan(plan),
        family_id="COMPOUND_AND_DEFAULT" if conjunctive else "COMPOUND_OR_DEFAULT",
        overrides_family=True,
    )


def _quarterly_plan(text: str) -> StructureMatch | None:
    """A ceiling/floor that must hold in every sub-period."""
    if _EVERY_QUARTER_RE.search(text) is None:
        return None
    restriction = _restriction_from(text)
    if restriction is None:
        return None
    actual, comparator, threshold = restriction
    if not isinstance(actual, PeriodAggregate):
        return None
    plan = CovenantPlan(
        reported_actual=actual,
        reported_actual_quantity_type=QuantityType.MONEY,
        activation_condition=Always(),
        breach_condition=Compare(
            left=actual, comparator=breach_comparator(comparator), right=threshold
        ),
        period_grouping=PeriodGrouping.FINANCIAL_QUARTER,
        period_basis=actual.basis,
    )
    return StructureMatch(
        plan=finalize_plan(plan), family_id="PERIOD_EXTREMA", overrides_family=True
    )


def _dynamic_threshold_plan(text: str) -> StructureMatch | None:
    """A covenant whose threshold is itself an expression."""
    restriction = _restriction_from(text)
    if restriction is None:
        return None
    actual, comparator, threshold = restriction
    if isinstance(threshold, Constant):
        return None
    plan = CovenantPlan(
        reported_actual=actual,
        reported_actual_quantity_type=QuantityType.MONEY,
        activation_condition=Always(),
        breach_condition=Compare(
            left=actual, comparator=breach_comparator(comparator), right=threshold
        ),
    )
    return StructureMatch(
        plan=finalize_plan(plan), family_id="DYNAMIC_THRESHOLD", overrides_family=True
    )


def _capped_addback_plan(text: str) -> StructureMatch | None:
    """Adjusted EBITDA with add-backs capped at a share of another metric."""
    low = text.casefold()
    if "ebitda" not in low:
        return None
    if not re.search(r"(?i)скорректированн\w*|adjusted", text):
        return None
    cap = _dynamic_threshold(text, after=0)
    if cap is None:
        return None
    scope = _scope_for(text)
    adjusted = Add(
        left=_ebitda(scope),
        right=Min(args=(_metric(MetricCategory.ONE_TIME_ADD_BACKS, scope), cap)),
    )
    leverage = _leverage_expr(text)
    if leverage is None:
        return None
    actual: Expr = Divide(
        numerator=leverage.numerator if isinstance(leverage, Divide) else leverage,
        denominator=adjusted,
    )
    threshold = _literal_threshold(text)
    comparator = _comparator_in(text)
    if threshold is None or comparator is None:
        return None
    plan = CovenantPlan(
        reported_actual=actual,
        reported_actual_quantity_type=QuantityType.RATIO,
        activation_condition=Always(),
        breach_condition=Compare(
            left=actual,
            comparator=breach_comparator(comparator),
            right=Constant(quantity=threshold),
        ),
    )
    return StructureMatch(
        plan=finalize_plan(plan), family_id="CAPPED_ADDBACK_LEVERAGE", overrides_family=True
    )


_STRUCTURE_PARSERS = (
    _springing_plan,
    _capped_addback_plan,
    _compound_plan,
    _quarterly_plan,
    _dynamic_threshold_plan,
)


def match_structure(clause_text: str) -> StructureMatch | None:
    """Return the first structural plan that parses, most specific first."""
    text = _norm(clause_text)
    # An inactive-when-below proviso is the ELSE arm of a springing covenant and
    # must never be read as a document status or as an unconditional restriction.
    for parser in _STRUCTURE_PARSERS:
        try:
            match = parser(text)
        except (CovenantTypeError, ValueError):
            continue
        if match is not None:
            return match
    if _UNTIL_INACTIVE_RE.search(text):
        return None
    return None
