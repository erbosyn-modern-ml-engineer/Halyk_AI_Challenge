"""Extract covenant-side modifiers retained for later authoritative fact stages."""

# ruff: noqa: RUF001

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from halyk_agent.domain.covenants.locate import build_ws_map
from halyk_agent.domain.covenants.models import CovenantModifier, CovenantModifierKind


@dataclass(frozen=True, slots=True)
class ModifierMatch:
    """One semantic recognition event: type + exact source quote fragment(s)."""

    kind: CovenantModifierKind
    detail: str
    reason_code: str
    quotes: tuple[str, ...]


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


def _sentence_window(norm: str, pos: int) -> tuple[int, int]:
    """Return [start, end) of the sentence containing ``pos``, else a radius window."""
    break_chars = {".", "!", "?", ";", "\n"}
    start = pos
    while start > 0 and norm[start - 1] not in break_chars:
        start -= 1
    end = pos
    n = len(norm)
    while end < n and norm[end] not in break_chars:
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


def _collect_matches(clause_text: str) -> list[ModifierMatch]:
    raw, idx_map, norm = _norm_map(clause_text)
    found: list[ModifierMatch] = []

    def add(
        kind: CovenantModifierKind,
        detail: str,
        reason_code: str,
        *matches: re.Match[str],
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
            )
        )

    # Materiality floor.
    m = _search(norm, r"порог\w*\s+существенност\w*|materialit(?:y|ies)\b")
    if m:
        add(
            CovenantModifierKind.MATERIALITY_FLOOR,
            "materiality floor for add-backs / adjustments",
            "MATERIALITY_FLOOR",
            m,
        )

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
        CovenantModifier(kind=item.kind, detail=item.detail, evidence_span_ids=())
        for item in extract_modifier_matches(clause_text)
    )
