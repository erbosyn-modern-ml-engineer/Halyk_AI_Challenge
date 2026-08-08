"""Extract covenant-side modifiers retained for later authoritative fact stages."""

# ruff: noqa: RUF001

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from halyk_agent.domain.covenants.ast import MetricCategory
from halyk_agent.domain.covenants.locate import build_ws_map
from halyk_agent.domain.covenants.models import CovenantModifier, CovenantModifierKind
from halyk_agent.domain.covenants.parse import (
    extend_span_through_money_tokens,
    scan_money_quantities,
)
from halyk_agent.domain.covenants.quantity import TypedQuantity


@dataclass(frozen=True, slots=True)
class ModifierMatch:
    """One semantic recognition event: type + exact source quote fragment(s)."""

    kind: CovenantModifierKind
    detail: str
    reason_code: str
    quotes: tuple[str, ...]
    threshold: TypedQuantity | None = None
    applies_to_category: MetricCategory | None = None


class ReclassificationPolarity(StrEnum):
    """Local polarity for one reclassification/adjustment instruction."""

    INCLUDE = "INCLUDE"
    EXCLUDE = "EXCLUDE"
    UNKNOWN = "UNKNOWN"
    CONFLICT = "CONFLICT"


# Polarity cues are resolved inside a bounded local window around an instruction
# anchor (sentence when available, otherwise ±WINDOW_RADIUS chars). Whole-clause
# keyword presence is intentionally NOT used for polarity.
_WINDOW_RADIUS = 180

_EXCLUDE_CUE_RE = re.compile(
    r"исключа\w*\s+из\s+расчёта|"
    r"из\s+расчёта\s+исключа\w*|"
    r"не\s+засчитыва\w*|"
    r"не\s+учитыва\w*|"
    r"не\s+принима\w*\s+в\s+расчёт|"
    r"shall\s+not\s+be\s+counted|"
    r"are\s+not\s+counted|"
    r"excluded\s+from\s+(?:the\s+)?(?:covenant\s+)?calculation|"
    r"shall\s+be\s+excluded|"
    r"disregarded\s+for\s+covenant|"
    r"not\s+included\s+in\s+the\s+calculation",
    re.IGNORECASE,
)

_INCLUDE_CUE_RE = re.compile(
    r"с\s+учёт[ое]м\s+(?:любой\s+)?"
    r"(?:переквалификац\w*|корректировк\w*)|"
    r"учитыва\w*\s+при\s+расчёте|"
    r"учитыва\w*\s+по\s+переклассифицированн\w*|"
    r"включа\w*\s+в\s+(?:настоящий\s+)?расчёт|"
    r"включая\s+суммы|"
    r"признают\s+подлежащей\s+отражению|"
    r"должн\w*\s+быть\s+отражен|"
    r"принима\w*\s+для\s+целей\s+расчёта|"
    r"taking\s+into\s+account|"
    r"shall\s+be\s+included|"
    r"included\s+in\s+the\s+(?:covenant\s+)?calculation|"
    r"shall\s+be\s+reflected|"
    r"are\s+reflected|"
    r"taken\s+into\s+account|"
    r"recognized\s+as|"
    r"treated\s+as.{0,40}?for\s+purposes",
    re.IGNORECASE | re.DOTALL,
)

_RECLASS_ANCHOR_RE = re.compile(
    r"переквалификац\w*|"
    r"переквалифицированн\w*|"
    r"переклассифицированн\w*|"
    r"reclassifications?|"
    r"reclassified",
    re.IGNORECASE,
)

_ADJUSTMENT_ANCHOR_RE = re.compile(
    r"корректировк\w*\s+по\s+методу\s+начислен\w*|"
    r"переквалификац\w*\s+периода|"
    r"accrual\s+adjustments?|"
    r"period\s+reclassification(?:s)?|"
    r"subject\s+to\s+(?:auditor\s+)?(?:accrual\s+)?adjustments?",
    re.IGNORECASE,
)

_NOISE_RE = re.compile(
    r"сотрудник|должност|адрес|employee|department|administrative|"
    r"обсужда|discussed\s+possible|discussed\s+the\s+report",
    re.IGNORECASE,
)


def _norm_map(clause_text: str) -> tuple[str, list[int], str]:
    raw = clause_text.replace("\xa0", " ")
    norm, idx_map = build_ws_map(raw)
    return raw, idx_map, norm


def _quote_from_match(raw: str, idx_map: list[int], match: re.Match[str]) -> str:
    start = idx_map[match.start()]
    end = idx_map[match.end() - 1] + 1
    return raw[start:end]


def _search(norm: str, pattern: str) -> re.Match[str] | None:
    return re.search(pattern, norm, flags=re.IGNORECASE | re.DOTALL)


def _is_decimal_dot(norm: str, index: int) -> bool:
    """True when ``norm[index]`` is a decimal point inside a numeric money/ratio token."""
    if index < 0 or index >= len(norm) or norm[index] != ".":
        return False
    return (
        index > 0
        and index + 1 < len(norm)
        and norm[index - 1].isdigit()
        and norm[index + 1].isdigit()
    )


# Legal / clause abbreviations whose trailing '.' must not end an instruction region.
_ABBREV_DOT_RE = re.compile(
    r"(?:^|[\s(])(?:п|пп|ст|cl|sec|art|no|nos|vol|fig|vs)\.$",
    re.IGNORECASE,
)


def _is_abbrev_dot(norm: str, index: int) -> bool:
    """True when ``norm[index]`` is '.' in a short legal abbreviation (п. / Sec. / cl.)."""
    if index < 0 or index >= len(norm) or norm[index] != ".":
        return False
    window = norm[max(0, index - 6) : index + 1]
    return _ABBREV_DOT_RE.search(window) is not None


def _is_sentence_break(norm: str, index: int) -> bool:
    if index < 0 or index >= len(norm):
        return False
    ch = norm[index]
    if ch in {"!", "?", ";", "\n"}:
        return True
    if ch != ".":
        return False
    return not (_is_decimal_dot(norm, index) or _is_abbrev_dot(norm, index))


def _sentence_window(norm: str, pos: int) -> tuple[int, int]:
    """Return [start, end) of the sentence containing ``pos``, else a radius window."""
    start = pos
    while start > 0 and not _is_sentence_break(norm, start - 1):
        start -= 1
    end = pos
    n = len(norm)
    while end < n and not _is_sentence_break(norm, end):
        end += 1
    if end < n:
        end += 1
    # Guard against pathological single-token sentences: expand to radius.
    if end - start < 24:
        start = max(0, pos - _WINDOW_RADIUS)
        end = min(n, pos + _WINDOW_RADIUS)
    return start, end


def _local_window(norm: str, match: re.Match[str]) -> tuple[int, int, str]:
    """
    Bounded local context for polarity.

    Prefer the containing sentence, but always expand to at least
    ±WINDOW_RADIUS around the match so OCR mid-clause separators
    (e.g. ';') do not sever a single instruction.
    """
    sent0, sent1 = _sentence_window(norm, match.start())
    start = min(sent0, max(0, match.start() - _WINDOW_RADIUS))
    end = max(sent1, min(len(norm), match.end() + _WINDOW_RADIUS))
    return start, end, norm[start:end]


def _tight_slice(norm: str, center_start: int, center_end: int, radius: int = 50) -> str:
    return norm[max(0, center_start - radius) : min(len(norm), center_end + radius)]


def _polarity_for_reclass(
    norm: str,
    *,
    reclass_start: int,
    reclass_end: int,
    wide_window: str,
) -> ReclassificationPolarity:
    """
    Resolve polarity for one reclassification anchor.

    Primary scope is the sentence containing the reclass token so a neighboring
    sentence with the opposite polarity does not create a false CONFLICT.
    """
    sent0, sent1 = _sentence_window(norm, reclass_start)
    sentence = norm[sent0:sent1]
    sent_ex = _EXCLUDE_CUE_RE.search(sentence) is not None
    sent_in = _INCLUDE_CUE_RE.search(sentence) is not None
    if sent_ex and sent_in:
        return ReclassificationPolarity.CONFLICT
    if sent_ex:
        return ReclassificationPolarity.EXCLUDE
    if sent_in:
        return ReclassificationPolarity.INCLUDE
    # Fallback: tight band around the token (OCR may omit sentence punctuation).
    tight = _tight_slice(norm, reclass_start, reclass_end, radius=50)
    tight_ex = _EXCLUDE_CUE_RE.search(tight) is not None
    tight_in = _INCLUDE_CUE_RE.search(tight) is not None
    if tight_ex and tight_in:
        return ReclassificationPolarity.CONFLICT
    if tight_ex:
        return ReclassificationPolarity.EXCLUDE
    if tight_in:
        return ReclassificationPolarity.INCLUDE
    wide_ex = _EXCLUDE_CUE_RE.search(wide_window) is not None
    wide_in = _INCLUDE_CUE_RE.search(wide_window) is not None
    if wide_ex and not wide_in:
        return ReclassificationPolarity.EXCLUDE
    if wide_in and not wide_ex:
        return ReclassificationPolarity.INCLUDE
    if wide_ex and wide_in:
        return ReclassificationPolarity.CONFLICT
    return ReclassificationPolarity.UNKNOWN


def _polarity_in_window(window: str) -> ReclassificationPolarity:
    """Fallback polarity when no single reclass anchor is available."""
    has_exclude = _EXCLUDE_CUE_RE.search(window) is not None
    has_include = _INCLUDE_CUE_RE.search(window) is not None
    if has_exclude and has_include:
        return ReclassificationPolarity.CONFLICT
    if has_exclude:
        return ReclassificationPolarity.EXCLUDE
    if has_include:
        return ReclassificationPolarity.INCLUDE
    return ReclassificationPolarity.UNKNOWN


def _nearest_anchor(
    pattern: re.Pattern[str],
    norm: str,
    *,
    w0: int,
    w1: int,
    cue_start: int,
    cue_end: int,
) -> re.Match[str] | None:
    """Pick the reclass/adjustment anchor nearest to the polarity cue.

    Prefer anchors in the same sentence as the cue when available.
    """
    sent0, sent1 = _sentence_window(norm, cue_start)
    same_sentence: list[re.Match[str]] = []
    all_matches: list[re.Match[str]] = []
    for match in pattern.finditer(norm, w0, w1):
        all_matches.append(match)
        if match.start() >= sent0 and match.end() <= sent1:
            same_sentence.append(match)
    pool = same_sentence or all_matches
    best: re.Match[str] | None = None
    best_dist: int | None = None
    for match in pool:
        if match.end() < cue_start:
            dist = cue_start - match.end()
        elif match.start() > cue_end:
            dist = match.start() - cue_end
        else:
            dist = 0
        if best_dist is None or dist < best_dist:
            best = match
            best_dist = dist
    return best


def _financial_context(window: str) -> bool:
    low = window.casefold()
    return any(
        tok in low
        for tok in (
            "аудитор",
            "ковенант",
            "auditor",
            "covenant",
            "переквалиф",
            "переклассиф",
            "reclass",
            "выруч",
            "revenue",
            "отчётн",
            "financ",
            "капитальн",
            "capex",
            "процентн",
            "interest",
        )
    )


_MATERIALITY_CUE_RE = re.compile(
    r"порог\w*\s+существенност\w*|materialit(?:y|ies)\b",
    re.IGNORECASE,
)
# Locate add-back / one-time floor *instructions* (not a single captured money token).
# Money candidates are collected from the bounded instruction region afterwards.
_ADD_BACK_FLOOR_RE = re.compile(
    r"(?:"
    r"разов\w*.{0,160}?не\s+менее\s+"
    r"(?:\$|USD\s*|€|EUR\s*|₸|KZT\s*)"
    r"|"
    r"не\s+менее\s+"
    r"(?:\$|USD\s*|€|EUR\s*|₸|KZT\s*)"
    r".{0,100}?(?:к\s+EBITDA\s+не\s+прибавля|не\s+прибавля\w*\s+к\s+EBITDA|"
    r"add[- ]?backs?\s+below|below\s+materiality)"
    r"|"
    r"(?:one[- ]?time|add[- ]?back)\w*.{0,120}?not\s+less\s+than\s+"
    r"(?:\$|USD\s*|€|EUR\s*|₸|KZT\s*)"
    r")",
    re.IGNORECASE | re.DOTALL,
)
_ADD_BACK_CATEGORY_CUE_RE = re.compile(
    r"разов\w*|one[- ]?time|add[- ]?back|EBITDA\s+не\s+прибавля|прибавля\w*\s+к\s+EBITDA",
    re.IGNORECASE,
)


def _instruction_region(
    norm: str,
    match: re.Match[str],
    *,
    anchor_at_match_start: bool,
) -> tuple[int, int, str]:
    """
    Bounded instruction region for materiality money collection.

    The region always covers the complete regex match (``end >= match.end()``), then
    expands forward to the sentence containing ``match.end() - 1`` so legal
    abbreviations (``п.`` / ``Sec.``) that break the *start* sentence cannot truncate
    the instruction to its first money token.

    For add-back instructions, ``anchor_at_match_start=True`` prevents walking
    backward through OCR tables / line-item amounts in one whitespace-normalized
    paragraph.
    """
    start_sent0, _start_sent1 = _sentence_window(norm, match.start())
    start = match.start() if anchor_at_match_start else min(start_sent0, match.start())
    end_anchor = match.end() - 1 if match.end() > match.start() else match.start()
    _end_sent0, end_sent1 = _sentence_window(norm, end_anchor)
    end = max(match.end(), end_sent1)
    end = extend_span_through_money_tokens(norm, start, end)
    return start, end, norm[start:end]


def _region_quote(raw: str, idx_map: list[int], region_start: int, region_end: int) -> str | None:
    if region_end <= region_start:
        return None
    return raw[idx_map[region_start] : idx_map[region_end - 1] + 1]


def _reconcile_materiality_regions(
    raw: str,
    idx_map: list[int],
    regions: list[tuple[int, int, str, MetricCategory | None]],
) -> ModifierMatch | None:
    """
    Document-level materiality reconciliation across bounded instruction regions.

    Semantics:
      - any region with malformed threshold-like money → no confident modifier
      - 0 distinct typed floors → no modifier
      - 1 distinct typed floor → publish (identical repeats dedupe)
      - >1 distinct (currency, Decimal) floors → ambiguous / no modifier

    No first/last/highest/lowest preference.
    """
    if not regions:
        return None
    seen: dict[tuple[str, object], TypedQuantity] = {}
    quotes: list[str] = []
    category: MetricCategory | None = None
    has_malformed = False
    for region_start, region_end, window, region_category in regions:
        scan = scan_money_quantities(window)
        if scan.has_malformed:
            has_malformed = True
            continue
        if not scan.quantities:
            continue
        quote = _region_quote(raw, idx_map, region_start, region_end)
        if quote is None:
            continue
        quotes.append(quote)
        if region_category is not None:
            category = region_category
        for quantity in scan.quantities:
            if quantity.currency is None:
                continue
            key = (quantity.currency, quantity.value)
            if key not in seen:
                seen[key] = quantity
    if has_malformed:
        return None
    if len(seen) != 1 or not quotes:
        return None
    threshold = next(iter(seen.values()))
    return ModifierMatch(
        kind=CovenantModifierKind.MATERIALITY_FLOOR,
        detail="materiality floor for add-backs / adjustments",
        reason_code="MATERIALITY_FLOOR",
        quotes=tuple(dict.fromkeys(quotes)),
        threshold=threshold,
        applies_to_category=category,
    )


def _materiality_match(
    raw: str,
    idx_map: list[int],
    norm: str,
) -> ModifierMatch | None:
    """
    Emit MATERIALITY_FLOOR only when an unambiguous typed monetary threshold
    is present after reconciling *all* materiality / add-back-floor instructions.

    Bare materiality language, malformed money, or competing distinct floors fail closed.
    """
    regions: list[tuple[int, int, str, MetricCategory | None]] = []

    floor_matches = list(_ADD_BACK_FLOOR_RE.finditer(norm))
    for floor in floor_matches:
        w0, w1, window = _instruction_region(norm, floor, anchor_at_match_start=True)
        regions.append((w0, w1, window, MetricCategory.ONE_TIME_ADD_BACKS))

    # Materiality cue regions also participate (same document-level reconciliation).
    # Skip cues fully inside an already-collected add-back instruction span.
    covered = [(a, b) for a, b, _w, _c in regions]
    for cue in _MATERIALITY_CUE_RE.finditer(norm):
        if any(a <= cue.start() < b for a, b in covered):
            continue
        w0, w1, window = _instruction_region(norm, cue, anchor_at_match_start=False)
        region_category = (
            MetricCategory.ONE_TIME_ADD_BACKS if _ADD_BACK_CATEGORY_CUE_RE.search(window) else None
        )
        regions.append((w0, w1, window, region_category))

    return _reconcile_materiality_regions(raw, idx_map, regions)


def _collect_matches(clause_text: str) -> list[ModifierMatch]:
    raw, idx_map, norm = _norm_map(clause_text)
    found: list[ModifierMatch] = []

    def add(
        kind: CovenantModifierKind,
        detail: str,
        reason_code: str,
        *matches: re.Match[str],
        threshold: TypedQuantity | None = None,
        applies_to_category: MetricCategory | None = None,
    ) -> None:
        quotes = tuple(_quote_from_match(raw, idx_map, m) for m in matches if m is not None)
        if not quotes:
            return
        found.append(
            ModifierMatch(
                kind=kind,
                detail=detail,
                reason_code=reason_code,
                quotes=quotes,
                threshold=threshold,
                applies_to_category=applies_to_category,
            )
        )

    materiality = _materiality_match(raw, idx_map, norm)
    if materiality is not None:
        found.append(materiality)

    # Numerator + denominator reclass treatment.
    m = _search(
        norm,
        r"переклассифицированн\w*.{0,120}?числител\w*.{0,60}?знаменател\w*|"
        r"как в числителе,\s*так и в знаменателе|"
        r"both\s+(?:the\s+)?numerator\s+and\s+(?:the\s+)?denominator",
    )
    if m:
        add(
            CovenantModifierKind.BOTH_NUMERATOR_AND_DENOMINATOR_RECLASS,
            "auditor reclassifications apply to numerator and denominator",
            "BOTH_NUM_DENOM_RECLASS",
            m,
        )

    # Rejected reclassifications are excluded (typed family, not generic INCLUDE/EXCLUDE).
    m = _search(
        norm,
        r"отклон[её]нн\w*\s+аудитор\w*|"
        r"рассмотренн\w*\s+и\s+отклон[её]нн\w*\s+аудитор\w*|"
        r"rejected\s+by\s+the\s+auditor|"
        r"reclassifications?\s+rejected\s+by\s+(?:the\s+)?auditor",
    )
    if m:
        add(
            CovenantModifierKind.REJECTED_RECLASSIFICATION_EXCLUDE,
            "reclassifications considered and rejected by auditors are excluded",
            "REJECTED_RECLASS_EXCLUDE",
            m,
        )

    # --- Polarity-aware reclassification / adjustment instructions ---
    # Scan exclude cues and include cues separately so two genuine instructions
    # in one clause can emit both INCLUDE and EXCLUDE from distinct windows.

    emitted_exclude_spans: list[tuple[int, int]] = []
    emitted_include_spans: list[tuple[int, int]] = []

    def _overlaps(span: tuple[int, int], seen: list[tuple[int, int]]) -> bool:
        a0, a1 = span
        return any(a0 < b1 and b0 < a1 for b0, b1 in seen)

    # EXCLUDE instructions: polarity cue + nearby reclass anchor in the same local window.
    for exclude_m in _EXCLUDE_CUE_RE.finditer(norm):
        w0, w1, window = _local_window(norm, exclude_m)
        if _NOISE_RE.search(window):
            continue
        reclass_m = _nearest_anchor(
            _RECLASS_ANCHOR_RE,
            norm,
            w0=w0,
            w1=w1,
            cue_start=exclude_m.start(),
            cue_end=exclude_m.end(),
        )
        if reclass_m is None:
            continue
        abs_reclass = reclass_m
        polarity = _polarity_for_reclass(
            norm,
            reclass_start=abs_reclass.start(),
            reclass_end=abs_reclass.end(),
            wide_window=window,
        )
        if polarity is ReclassificationPolarity.CONFLICT:
            continue
        if polarity is not ReclassificationPolarity.EXCLUDE:
            continue
        span = (
            min(abs_reclass.start(), exclude_m.start()),
            max(abs_reclass.end(), exclude_m.end()),
        )
        if _overlaps(span, emitted_exclude_spans):
            continue
        emitted_exclude_spans.append(span)
        add(
            CovenantModifierKind.AUDITOR_RECLASSIFICATION_EXCLUDE,
            "reclassified amounts are excluded from the covenant calculation",
            "RECLASS_EXCLUDE_LOCAL",
            abs_reclass,
            exclude_m,
        )

    # INCLUDE instructions: require positive inclusion polarity in the local window.
    # Bare "reclassified by the auditor" without include cues does NOT emit INCLUDE.
    for include_m in _INCLUDE_CUE_RE.finditer(norm):
        w0, w1, window = _local_window(norm, include_m)
        if _NOISE_RE.search(window):
            continue
        # Need a reclass/adjustment anchor in the same window (cue alone is insufficient
        # unless the cue already embeds the reclass noun, which _INCLUDE_CUE_RE covers).
        has_reclass = _RECLASS_ANCHOR_RE.search(window) is not None
        has_adjustment = _ADJUSTMENT_ANCHOR_RE.search(window) is not None
        cue_text = include_m.group(0)
        cue_embeds_reclass = bool(
            re.search(
                r"переквалификац|переклассиф|reclass|корректировк|accrual|adjustment",
                cue_text,
                re.IGNORECASE,
            )
        )
        # Auditor reflection / calculation-inclusion cues are load-bearing even
        # without an explicit "reclass*" token (e.g. "признают подлежащей отражению").
        standalone_include = bool(
            re.search(
                r"признают\s+подлежащей\s+отражению|"
                r"учитыва\w*\s+при\s+расчёте|"
                r"shall\s+be\s+included|"
                r"included\s+in\s+the\s+(?:covenant\s+)?calculation|"
                r"shall\s+be\s+reflected|"
                r"are\s+reflected",
                cue_text,
                re.IGNORECASE,
            )
        )
        if not (has_reclass or has_adjustment or cue_embeds_reclass or standalone_include):
            continue
        if not _financial_context(window) and not cue_embeds_reclass:
            continue

        extra: re.Match[str] | None = None
        adj = _nearest_anchor(
            _ADJUSTMENT_ANCHOR_RE,
            norm,
            w0=w0,
            w1=w1,
            cue_start=include_m.start(),
            cue_end=include_m.end(),
        )
        rec = _nearest_anchor(
            _RECLASS_ANCHOR_RE,
            norm,
            w0=w0,
            w1=w1,
            cue_start=include_m.start(),
            cue_end=include_m.end(),
        )
        if adj is not None:
            extra = adj
        elif rec is not None and rec.group(0).casefold() not in cue_text.casefold():
            extra = rec

        if extra is not None and _RECLASS_ANCHOR_RE.search(extra.group(0)):
            polarity = _polarity_for_reclass(
                norm,
                reclass_start=extra.start(),
                reclass_end=extra.end(),
                wide_window=window,
            )
        else:
            polarity = _polarity_in_window(window)
        if polarity is ReclassificationPolarity.CONFLICT:
            continue
        if polarity is not ReclassificationPolarity.INCLUDE:
            continue

        span = (include_m.start(), include_m.end())
        if extra is not None:
            span = (min(span[0], extra.start()), max(span[1], extra.end()))
        if _overlaps(span, emitted_include_spans):
            continue
        # Do not emit INCLUDE for a span already classified as EXCLUDE.
        if _overlaps(span, emitted_exclude_spans):
            continue
        emitted_include_spans.append(span)
        if extra is not None:
            add(
                CovenantModifierKind.AUDITOR_RECLASSIFICATION_INCLUDE,
                "auditor/accounting reclassifications or adjustments must be reflected",
                "RECLASS_INCLUDE_LOCAL",
                include_m,
                extra,
            )
        else:
            add(
                CovenantModifierKind.AUDITOR_RECLASSIFICATION_INCLUDE,
                "auditor/accounting reclassifications or adjustments must be reflected",
                "RECLASS_INCLUDE_LOCAL",
                include_m,
            )

    # Accrual / period adjustment INCLUDE when financial context is present and
    # local polarity is not EXCLUDE (covers B4-like wording without a separate
    # "s uchetom" include cue already consumed above).
    for adj_m in _ADJUSTMENT_ANCHOR_RE.finditer(norm):
        w0, w1, window = _local_window(norm, adj_m)
        if _NOISE_RE.search(window):
            continue
        if not _financial_context(window):
            continue
        polarity = _polarity_in_window(window)
        if polarity is ReclassificationPolarity.EXCLUDE:
            continue
        if polarity is ReclassificationPolarity.CONFLICT:
            continue
        # If an include cue already covered this span, skip.
        span = (adj_m.start(), adj_m.end())
        if _overlaps(span, emitted_include_spans) or _overlaps(span, emitted_exclude_spans):
            continue
        # Require either explicit include polarity or auditor/covenant acceptance context.
        if polarity is ReclassificationPolarity.UNKNOWN and not re.search(
            r"принят\w*\s+аудитор|approved\s+by\s+(?:the\s+)?auditor|аудитор|auditor|ковенант|covenant",
            window,
            re.IGNORECASE,
        ):
            continue
        emitted_include_spans.append(span)
        add(
            CovenantModifierKind.AUDITOR_RECLASSIFICATION_INCLUDE,
            "auditor/accounting reclassifications or adjustments must be reflected",
            "ADJUSTMENT_INCLUDE_LOCAL",
            adj_m,
        )

    return found


def extract_modifier_matches(clause_text: str) -> tuple[ModifierMatch, ...]:
    """Return deterministic modifier matches with exact source quote fragments."""
    found = _collect_matches(clause_text)
    # Dedup by kind for singleton families; allow both INCLUDE and EXCLUDE when
    # they originated from separate local instruction windows.
    singleton_kinds = {
        CovenantModifierKind.MATERIALITY_FLOOR.value,
        CovenantModifierKind.BOTH_NUMERATOR_AND_DENOMINATOR_RECLASS.value,
        CovenantModifierKind.REJECTED_RECLASSIFICATION_EXCLUDE.value,
        CovenantModifierKind.AUDITOR_RECLASSIFICATION_INCLUDE.value,
        CovenantModifierKind.AUDITOR_RECLASSIFICATION_EXCLUDE.value,
    }
    seen: set[str] = set()
    out: list[ModifierMatch] = []
    for item in found:
        # One INCLUDE and one EXCLUDE max per clause keeps output stable while
        # still allowing dual-instruction polarity (different kinds).
        if item.kind.value in singleton_kinds:
            if item.kind.value in seen:
                continue
            seen.add(item.kind.value)
        out.append(item)
    return tuple(out)


def extract_modifiers(clause_text: str) -> tuple[CovenantModifier, ...]:
    """Back-compat wrapper without evidence ids (compiler attaches spans)."""
    return tuple(
        CovenantModifier(
            kind=item.kind,
            detail=item.detail,
            evidence_span_ids=(),
            threshold=item.threshold,
            applies_to_category=item.applies_to_category,
        )
        for item in extract_modifier_matches(clause_text)
    )
