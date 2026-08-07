"""Generic comparator / threshold / period / scope parsers."""

# ruff: noqa: RUF001

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from halyk_agent.domain.covenants.ast import TransactionSelector
from halyk_agent.domain.covenants.models import (
    BoundaryInclusivity,
    Comparator,
    PeriodDefinition,
    PeriodKind,
    ScopeDefinition,
    ScopeKind,
    ScopeProvenance,
)
from halyk_agent.domain.covenants.quantity import QuantityType, TypedQuantity
from halyk_agent.domain.transactions import coerce_decimal_amount


@dataclass(frozen=True, slots=True)
class ParsedComparator:
    comparator: Comparator
    matched_text: str


class ThresholdRole(StrEnum):
    COMPLIANCE = "COMPLIANCE"
    ACTIVATION = "ACTIVATION"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ThresholdCandidate:
    quantity: TypedQuantity
    matched_text: str
    role: ThresholdRole
    context: str


@dataclass(frozen=True, slots=True)
class ThresholdParseResult:
    status: str  # ok | ambiguous | malformed | missing
    quantity: TypedQuantity | None = None
    matched_text: str | None = None
    reason: str | None = None
    candidates: tuple[ThresholdCandidate, ...] = ()


# Currency prefix + numeric body attempt (validated separately; no prefix truncation).
_MONEY_PREFIX_RE = re.compile(r"(?P<prefix>\$|USD\s+|EUR\s+|€|KZT\s+|₸)\s*", re.IGNORECASE)
_MONEY_BODY_RE = re.compile(r"[0-9][0-9,.]*")
_VALID_MONEY_NUM_RE = re.compile(r"^(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?$")
_RATIO_TOKEN_RE = re.compile(
    r"(?<![0-9.])(?P<num>\d+(?:\.\d+)?)\s*x\b(?!\w)",
    re.IGNORECASE,
)
_PERCENT_TOKEN_RE = re.compile(
    r"(?<![0-9.])(?P<num>\d+(?:\.\d+)?)\s*%(?!\w)",
)
_MALFORMED_RATIO_RE = re.compile(r"(?<![0-9])\d+(?:\.\d+){2,}\s*x\b", re.IGNORECASE)
# Bare currency with no following numeric body (do not fire on "USD 1,000").
_BARE_CURRENCY_RE = re.compile(
    r"(?<!\w)(?:\$|USD|EUR|€|KZT|₸)(?!\s*[0-9])",
    re.IGNORECASE,
)
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

_RELATED_PARTY_PHRASES = (
    r"связанн\w*\s+сторон",
    r"в\s+пользу\s+связанн",
    r"платеж\w*\s+связанн\w*\s+сторон",
    r"аффилир\w*\s+(?:лиц|сторон)",
    r"related[\s-]+part(?:y|ies)",
    r"payments?\s+to\s+related\s+part",
    r"related[\s-]+party\s+payments?",
)

_COMPLIANCE_CTX = (
    "не менее",
    "не ниже",
    "не более",
    "must not exceed",
    "not less than",
    "at least",
    "at most",
    "не допускать",
    "обязуется",
    "обеспечива",
    "не должны превышать",
    "не должно превышать",
    "на уровне не менее",
)
_ACTIVATION_CTX = (
    "только при условии",
    "при условии",
    "if financing",
    "springing",
    "если совокупн",
)


def _norm(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


def _currency_from_prefix(prefix: str) -> str | None:
    token = prefix.strip().upper().replace(" ", "")
    if token in {"$", "USD"}:
        return "USD"
    if token in {"€", "EUR"}:
        return "EUR"
    if token in {"₸", "KZT"}:
        return "KZT"
    return None


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
    if re.search(r"не\s+вправе\s+допускать.{0,160}?более\s+\d", low, re.DOTALL):
        m = re.search(r"более\s+\d", text, re.IGNORECASE)
        return ParsedComparator(Comparator.LTE, m.group(0) if m else "более")
    if re.search(r"свыше\s+\$", low):
        return ParsedComparator(Comparator.LTE, "свыше")

    for comparator, pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            if comparator is Comparator.LT and "не допускать снижения" in low:
                continue
            return ParsedComparator(comparator, match.group(0))
    return None


def _role_for_window(window: str) -> ThresholdRole:
    """Classify threshold role using cue proximity to the numeric token (window end)."""
    low = window.casefold()

    def _last_pos(tokens: tuple[str, ...]) -> int:
        best = -1
        for tok in tokens:
            pos = low.rfind(tok)
            if pos > best:
                best = pos
        return best

    act_pos = _last_pos(_ACTIVATION_CTX)
    comp_pos = _last_pos(_COMPLIANCE_CTX)
    if act_pos < 0 and comp_pos < 0:
        # Weak fallback: Russian/English "exceed*" near amount without obligation verb.
        if "превыш" in low or "exceed" in low:
            return ThresholdRole.COMPLIANCE
        return ThresholdRole.UNKNOWN
    if act_pos >= 0 and (comp_pos < 0 or act_pos > comp_pos):
        return ThresholdRole.ACTIVATION
    if comp_pos >= 0:
        return ThresholdRole.COMPLIANCE
    return ThresholdRole.ACTIVATION


def _iter_money_tokens(text: str) -> list[tuple[str, str, str, bool]]:
    """
    Yield (prefix, num_body, matched_text, is_valid) for each currency-prefixed amount attempt.

    Invalid bodies (e.g. $1,00,0.0.0) are reported as malformed rather than truncated.
    """
    out: list[tuple[str, str, str, bool]] = []
    for prefix_m in _MONEY_PREFIX_RE.finditer(text):
        rest = text[prefix_m.end() :]
        body_m = _MONEY_BODY_RE.match(rest)
        if body_m is None:
            out.append((prefix_m.group("prefix"), "", prefix_m.group(0).rstrip(), False))
            continue
        body = body_m.group(0)
        # Trim trailing separators that are clause punctuation, not part of the number.
        while body and body[-1] in {",", "."}:
            candidate = body[:-1]
            if _VALID_MONEY_NUM_RE.fullmatch(candidate):
                body = candidate
                break
            body = candidate
        matched = text[prefix_m.start() : prefix_m.end() + len(body_m.group(0))]
        # Prefer trimmed body length for matched_text when trailing punctuation was clipped.
        if body != body_m.group(0):
            matched = text[prefix_m.start() : prefix_m.end() + len(body)]
        valid = bool(_VALID_MONEY_NUM_RE.fullmatch(body))
        out.append((prefix_m.group("prefix"), body, matched, valid))
    return out


def collect_threshold_candidates(clause_text: str) -> tuple[ThresholdCandidate, ...]:
    text = _norm(clause_text)
    if has_malformed_threshold_token(text):
        return ()
    out: list[ThresholdCandidate] = []
    for match in _RATIO_TOKEN_RE.finditer(text):
        value = coerce_decimal_amount(match.group("num"))
        window = text[max(0, match.start() - 90) : match.end() + 20]
        out.append(
            ThresholdCandidate(
                quantity=TypedQuantity(quantity_type=QuantityType.RATIO, value=value),
                matched_text=match.group(0),
                role=_role_for_window(window),
                context=window,
            )
        )
    for prefix, num_body, matched_text, valid in _iter_money_tokens(text):
        if not valid:
            continue
        currency = _currency_from_prefix(prefix)
        if currency is None:
            continue
        value = coerce_decimal_amount(num_body.replace(",", ""))
        # Locate match for window context.
        pos = text.find(matched_text)
        window = text[max(0, pos - 90) : pos + len(matched_text) + 20] if pos >= 0 else matched_text
        out.append(
            ThresholdCandidate(
                quantity=TypedQuantity(
                    quantity_type=QuantityType.MONEY, value=value, currency=currency
                ),
                matched_text=matched_text,
                role=_role_for_window(window),
                context=window,
            )
        )
    for match in _PERCENT_TOKEN_RE.finditer(text):
        value = coerce_decimal_amount(match.group("num"))
        window = text[max(0, match.start() - 90) : match.end() + 20]
        out.append(
            ThresholdCandidate(
                quantity=TypedQuantity(quantity_type=QuantityType.PERCENT, value=value),
                matched_text=match.group(0),
                role=_role_for_window(window),
                context=window,
            )
        )
    return tuple(out)


def has_malformed_threshold_token(clause_text: str) -> bool:
    text = _norm(clause_text)
    if _MALFORMED_RATIO_RE.search(text):
        return True
    if _BARE_CURRENCY_RE.search(text):
        return True
    return any(not valid for _prefix, _body, _matched, valid in _iter_money_tokens(text))


def select_compliance_threshold(
    candidates: tuple[ThresholdCandidate, ...],
    *,
    prefer_ratio: bool = False,
) -> ThresholdParseResult:
    if not candidates:
        return ThresholdParseResult(status="missing", reason="no threshold candidates")
    compliance = [c for c in candidates if c.role is ThresholdRole.COMPLIANCE]
    unknown = [c for c in candidates if c.role is ThresholdRole.UNKNOWN]
    activation = [c for c in candidates if c.role is ThresholdRole.ACTIVATION]

    pool = compliance or (unknown if not activation else compliance)
    if prefer_ratio:
        ratio_pool = [
            c
            for c in (compliance or unknown or candidates)
            if c.quantity.quantity_type is QuantityType.RATIO
        ]
        if len(ratio_pool) == 1:
            hit = ratio_pool[0]
            return ThresholdParseResult(
                status="ok",
                quantity=hit.quantity,
                matched_text=hit.matched_text,
                candidates=candidates,
            )
        if len(ratio_pool) > 1:
            return ThresholdParseResult(
                status="ambiguous",
                reason="multiple ratio compliance thresholds",
                candidates=candidates,
            )

    if not pool:
        # Springing: only activation money left + need ratio separately.
        if prefer_ratio:
            return ThresholdParseResult(
                status="missing",
                reason="no ratio compliance threshold",
                candidates=candidates,
            )
        pool = unknown

    # Deduplicate identical (type,value,currency) compliance candidates.
    unique: dict[tuple[str, str, str | None], ThresholdCandidate] = {}
    for cand in pool:
        key = (
            cand.quantity.quantity_type.value,
            format(cand.quantity.value, "f"),
            cand.quantity.currency,
        )
        unique[key] = cand
    values = list(unique.values())
    if len(values) == 1:
        hit = values[0]
        return ThresholdParseResult(
            status="ok",
            quantity=hit.quantity,
            matched_text=hit.matched_text,
            candidates=candidates,
        )
    if len(values) > 1:
        return ThresholdParseResult(
            status="ambiguous",
            reason="multiple plausible compliance thresholds",
            candidates=candidates,
        )
    return ThresholdParseResult(
        status="missing", reason="no compliance threshold", candidates=candidates
    )


def parse_threshold(clause_text: str) -> ThresholdParseResult:
    if has_malformed_threshold_token(clause_text):
        return ThresholdParseResult(status="malformed", reason="malformed numeric threshold token")
    candidates = collect_threshold_candidates(clause_text)
    return select_compliance_threshold(candidates, prefer_ratio=False)


def parse_ratio_threshold(clause_text: str) -> ThresholdParseResult:
    if has_malformed_threshold_token(clause_text):
        return ThresholdParseResult(status="malformed", reason="malformed numeric threshold token")
    candidates = collect_threshold_candidates(clause_text)
    return select_compliance_threshold(candidates, prefer_ratio=True)


def parse_all_money(clause_text: str) -> list[tuple[TypedQuantity, str]]:
    out: list[tuple[TypedQuantity, str]] = []
    for cand in collect_threshold_candidates(clause_text):
        if cand.quantity.quantity_type is QuantityType.MONEY:
            out.append((cand.quantity, cand.matched_text))
    return out


def parse_period(clause_text: str) -> PeriodDefinition | None:
    text = _norm(clause_text)
    as_of = _AS_OF_RE.search(text)
    iso = _ISO_RANGE_RE.search(text)
    quarter = _QUARTER_RE.search(text)

    if quarter and ("квартал" in text.casefold() or "quarter" in text.casefold()):
        end = date.fromisoformat(quarter.group(1))
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

    if as_of and iso and "по состоянию на" in text.casefold():
        return PeriodDefinition(
            period_kind=PeriodKind.MIXED,
            as_of_date=date.fromisoformat(as_of.group(1)),
            start_date=date.fromisoformat(iso.group(1)),
            end_date=date.fromisoformat(iso.group(2)),
            flow_start_date=date.fromisoformat(iso.group(1)),
            flow_end_date=date.fromisoformat(iso.group(2)),
            start_inclusive=BoundaryInclusivity.INCLUSIVE,
            end_inclusive=BoundaryInclusivity.INCLUSIVE,
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


def related_party_phrase(clause_text: str) -> str | None:
    text = _norm(clause_text)
    for pattern in _RELATED_PARTY_PHRASES:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(0)
    # Reject generic related-expenses wording (not related-party scope).
    return None


def resolve_scope(
    clause_text: str,
    *,
    selectors: tuple[TransactionSelector, ...],
) -> ScopeDefinition:
    """
    Resolve top-level scope.

    RELATED_PARTY_SET requires explicit related-party phrasing or selector flag.
    Mixed group/borrower operand scopes become EXPRESSION_SCOPED.
    """
    text = _norm(clause_text)
    low = text.casefold()
    group_ops = any(s.group_level for s in selectors)
    borrower_ops = any(not s.group_level for s in selectors)
    rp_ops = any(s.related_party_only for s in selectors)
    rp_phrase = related_party_phrase(clause_text)

    if group_ops and borrower_ops:
        matched = None
        if "групп" in low:
            m = re.search(r"Групп\w*|групп\w*|Group", text)
            matched = m.group(0) if m else "GROUP"
        return ScopeDefinition(
            scope_kind=ScopeKind.EXPRESSION_SCOPED,
            provenance=ScopeProvenance.EXPRESSION_OPERAND_SCOPES,
            matched_text=matched,
        )

    if rp_ops or rp_phrase:
        return ScopeDefinition(
            scope_kind=ScopeKind.RELATED_PARTY_SET,
            provenance=(
                ScopeProvenance.SELECTOR_DERIVED
                if rp_ops and not rp_phrase
                else ScopeProvenance.EXPLICIT_TEXT
            ),
            matched_text=rp_phrase,
        )

    if group_ops or ("групп" in low and "капитальн" in low):
        m = re.search(r"Групп\w*|групп\w*|Group", text)
        return ScopeDefinition(
            scope_kind=ScopeKind.GROUP,
            provenance=ScopeProvenance.EXPLICIT_TEXT if m else ScopeProvenance.SELECTOR_DERIVED,
            matched_text=m.group(0) if m else None,
        )

    if "неограниченн" in low and "дочерн" in low:
        m = re.search(r"Неограниченн\w*\s+дочерн\w*|дочерн\w*", text, re.IGNORECASE)
        return ScopeDefinition(
            scope_kind=ScopeKind.BORROWER_AND_SUBSIDIARIES,
            provenance=ScopeProvenance.EXPLICIT_TEXT,
            matched_text=m.group(0) if m else None,
        )

    return ScopeDefinition(
        scope_kind=ScopeKind.BORROWER,
        provenance=ScopeProvenance.DEFAULT_BORROWER_BY_RULE,
        matched_text=None,
    )


# Back-compat thin wrapper used by older call sites/tests.
def parse_scope(clause_text: str) -> ScopeDefinition:
    return resolve_scope(clause_text, selectors=())
