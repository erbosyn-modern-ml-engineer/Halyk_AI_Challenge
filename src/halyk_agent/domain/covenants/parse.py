"""Generic comparator / threshold / period / scope parsers."""

# ruff: noqa: RUF001

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from halyk_agent.domain.covenants.models import (
    BoundaryInclusivity,
    Comparator,
    PeriodDefinition,
    PeriodKind,
    ScopeDefinition,
    ScopeKind,
)
from halyk_agent.domain.covenants.quantity import QuantityType, TypedQuantity
from halyk_agent.domain.transactions import coerce_decimal_amount


@dataclass(frozen=True, slots=True)
class ParsedComparator:
    comparator: Comparator
    matched_text: str


@dataclass(frozen=True, slots=True)
class ParsedThreshold:
    quantity: TypedQuantity
    matched_text: str


_MONEY_RE = re.compile(
    r"\$\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?)",
)
_RATIO_X_RE = re.compile(r"(?<![0-9])(\d+(?:\.\d+)?)\s*x\b", re.IGNORECASE)
_PERCENT_RE = re.compile(r"(?<![0-9])(\d+(?:[.,]\d+)?)\s*%")
_ISO_RANGE_RE = re.compile(
    r"(20\d{2}-\d{2}-\d{2})\s*(?:по|to|-|—|–)\s*(20\d{2}-\d{2}-\d{2})",
    re.IGNORECASE,
)
_AS_OF_RE = re.compile(
    r"(?:по\s+состоянию\s+на|as\s+of|as-at)\s*(20\d{2}-\d{2}-\d{2})",
    re.IGNORECASE,
)
_QUARTER_RE = re.compile(
    r"(?:четвёрт|четверт|fourth)\w*\s+(?:финансов\w+\s+)?квартал\w*.{0,80}?(20\d{2}-\d{2}-\d{2})",
    re.IGNORECASE | re.DOTALL,
)


def _norm(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


def parse_comparator(clause_text: str) -> ParsedComparator | None:
    text = _norm(clause_text)
    low = text.casefold()

    patterns: list[tuple[Comparator, str]] = [
        (Comparator.LTE, r"не\s+долж\w*\s+превыш\w*"),
        (Comparator.LTE, r"не\s+вправе\s+допускать.{0,80}?превыш"),
        (Comparator.LTE, r"обязуется\s+не\s+допускать.{0,120}?превыш"),
        (Comparator.LTE, r"не\s+превыш\w*"),
        (Comparator.LTE, r"must\s+not\s+exceed"),
        (Comparator.LTE, r"not\s+greater\s+than"),
        (Comparator.LTE, r"at\s+most"),
        (Comparator.LTE, r"не\s+более"),
        (Comparator.LTE, r"составили\s+более"),  # "не ... более" handled above; this alone is weak
        (Comparator.GTE, r"не\s+менее"),
        (Comparator.GTE, r"не\s+ниже"),
        (Comparator.GTE, r"на\s+уровне\s+не\s+менее"),
        (Comparator.GTE, r"составлял[оа]?\s+не\s+менее"),
        (Comparator.GTE, r"должно\s+составлять\s+не\s+менее"),
        (Comparator.GTE, r"обеспечива\w*.{0,40}?не\s+менее"),
        (Comparator.GTE, r"not\s+less\s+than"),
        (Comparator.GTE, r"at\s+least"),
        (Comparator.GTE, r"не\s+допускать\s+снижения.{0,40}?ниже"),
        (Comparator.GT, r"must\s+exceed"),
        (Comparator.GT, r"greater\s+than"),
        (Comparator.LT, r"less\s+than"),
        (Comparator.LT, r"ниже\s+величины"),
        (Comparator.EQ, r"must\s+equal"),
        (Comparator.EQ, r"равно\s+"),
    ]
    # Prefer longer / more specific matches by scanning in order (already ordered).
    # Special-case "составили более 0.05x" under "не вправе допускать ... более"
    if re.search(r"не\s+вправе\s+допускать.{0,160}?более\s+\d", low, re.DOTALL):
        m = re.search(r"более\s+\d", text, re.IGNORECASE)
        return ParsedComparator(Comparator.LTE, m.group(0) if m else "более")
    if re.search(r"свыше\s+\$", low):
        return ParsedComparator(Comparator.LTE, "свыше")

    for comparator, pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            # Avoid treating "не допускать снижения ... ниже" as LT when GTE intended.
            if comparator is Comparator.LT and "не допускать снижения" in low:
                continue
            return ParsedComparator(comparator, match.group(0))
    return None


def parse_ratio_threshold(clause_text: str) -> ParsedThreshold | None:
    """Parse the first/best Nx ratio threshold only."""
    text = _norm(clause_text)
    ratio_hits = list(_RATIO_X_RE.finditer(text))
    if not ratio_hits:
        return None
    hit = ratio_hits[0]
    for candidate in ratio_hits:
        window = text[max(0, candidate.start() - 80) : candidate.end() + 20].casefold()
        if any(
            tok in window
            for tok in ("отношен", "коэффициент", "ratio", "покрыт", "марж", "доля", "величиной")
        ):
            hit = candidate
            break
    value = coerce_decimal_amount(hit.group(1).replace(",", ""))
    return ParsedThreshold(
        TypedQuantity(quantity_type=QuantityType.RATIO, value=value),
        hit.group(0),
    )


def parse_threshold(clause_text: str) -> ParsedThreshold | None:
    text = _norm(clause_text)
    # Prefer ratio "Nx" near covenant verbs, else money, else percent.
    ratio_hits = list(_RATIO_X_RE.finditer(text))
    money_hits = list(_MONEY_RE.finditer(text))
    percent_hits = list(_PERCENT_RE.finditer(text))

    # Springing clauses contain both money (activation) and ratio (test).
    # Prefer the ratio that appears with "x" as the primary threshold when present
    # for ratio-style titles, but callers may override.
    if ratio_hits:
        # Choose the ratio closest to comparator language when multiple.
        hit = ratio_hits[0]
        if len(ratio_hits) > 1:
            # Prefer smaller decorative ratios near "отношен" / cover language.
            for candidate in ratio_hits:
                window = text[max(0, candidate.start() - 80) : candidate.end() + 20].casefold()
                if any(
                    tok in window
                    for tok in ("отношен", "коэффициент", "ratio", "покрыт", "марж", "доля")
                ):
                    hit = candidate
                    break
        value = coerce_decimal_amount(hit.group(1).replace(",", ""))
        return ParsedThreshold(
            TypedQuantity(quantity_type=QuantityType.RATIO, value=value),
            hit.group(0),
        )
    if money_hits:
        # For springing: primary money threshold may be the covenant money limit.
        # Prefer the money amount after "не менее/превыш/свыше" if present.
        hit = money_hits[0]
        for candidate in money_hits:
            window = text[max(0, candidate.start() - 60) : candidate.end()].casefold()
            if any(
                tok in window
                for tok in ("не менее", "не ниже", "превыш", "свыше", "не более", "exceed")
            ):
                hit = candidate
                break
        raw = hit.group(1).replace(",", "")
        value = coerce_decimal_amount(raw)
        return ParsedThreshold(
            TypedQuantity(quantity_type=QuantityType.MONEY, value=value, currency="USD"),
            hit.group(0),
        )
    if percent_hits:
        hit = percent_hits[0]
        raw = hit.group(1).replace(",", ".")
        value = coerce_decimal_amount(raw)
        return ParsedThreshold(
            TypedQuantity(quantity_type=QuantityType.PERCENT, value=value),
            hit.group(0),
        )
    return None


def parse_all_money(clause_text: str) -> list[tuple[TypedQuantity, str]]:
    out: list[tuple[TypedQuantity, str]] = []
    for hit in _MONEY_RE.finditer(_norm(clause_text)):
        value = coerce_decimal_amount(hit.group(1).replace(",", ""))
        out.append(
            (
                TypedQuantity(quantity_type=QuantityType.MONEY, value=value, currency="USD"),
                hit.group(0),
            )
        )
    return out


def parse_period(clause_text: str) -> PeriodDefinition | None:
    text = _norm(clause_text)
    as_of = _AS_OF_RE.search(text)
    iso = _ISO_RANGE_RE.search(text)
    quarter = _QUARTER_RE.search(text)

    if quarter and ("квартал" in text.casefold() or "quarter" in text.casefold()):
        end = date.fromisoformat(quarter.group(1))
        # Q4 ending YYYY-12-31 → start YYYY-10-01
        start = (
            date(end.year, 10, 1)
            if end.month == 12
            else date(end.year, ((end.month - 1) // 3) * 3 + 1, 1)
        )
        return PeriodDefinition(
            period_kind=PeriodKind.FINANCIAL_QUARTER,
            start_date=start,
            end_date=end,
            start_inclusive=BoundaryInclusivity.INCLUSIVE,
            end_inclusive=BoundaryInclusivity.INCLUSIVE,
            quarter=4 if end.month == 12 else ((end.month - 1) // 3) + 1,
        )

    if as_of and "по состоянию на" in text.casefold():
        return PeriodDefinition(
            period_kind=PeriodKind.AS_OF,
            as_of_date=date.fromisoformat(as_of.group(1)),
            start_date=date.fromisoformat(iso.group(1)) if iso else None,
            end_date=date.fromisoformat(iso.group(2)) if iso else None,
            start_inclusive=BoundaryInclusivity.INCLUSIVE,
            end_inclusive=BoundaryInclusivity.INCLUSIVE,
        )

    if iso:
        return PeriodDefinition(
            period_kind=PeriodKind.CLOSED_INTERVAL,
            start_date=date.fromisoformat(iso.group(1)),
            end_date=date.fromisoformat(iso.group(2)),
            start_inclusive=BoundaryInclusivity.INCLUSIVE,
            end_inclusive=BoundaryInclusivity.INCLUSIVE,
        )
    if as_of:
        return PeriodDefinition(
            period_kind=PeriodKind.AS_OF,
            as_of_date=date.fromisoformat(as_of.group(1)),
        )
    return None


def parse_scope(clause_text: str) -> ScopeDefinition:
    low = _norm(clause_text).casefold()
    if "групп" in low and ("капитальн" in low or "group" in low):
        return ScopeDefinition(scope_kind=ScopeKind.GROUP)
    if "дочерн" in low or "subsidiar" in low:
        return ScopeDefinition(scope_kind=ScopeKind.BORROWER_AND_SUBSIDIARIES)
    if "связанн" in low or "аффилир" in low or "related-party" in low or "related party" in low:
        # Related-party covenants still measure borrower payments to RP set.
        return ScopeDefinition(scope_kind=ScopeKind.RELATED_PARTY_SET)
    return ScopeDefinition(scope_kind=ScopeKind.BORROWER)
