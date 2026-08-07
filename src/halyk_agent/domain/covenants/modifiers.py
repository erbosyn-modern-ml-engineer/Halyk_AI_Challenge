"""Extract covenant-side modifiers retained for later authoritative fact stages."""

# ruff: noqa: RUF001

from __future__ import annotations

import re
from dataclasses import dataclass

from halyk_agent.domain.covenants.locate import build_ws_map
from halyk_agent.domain.covenants.models import CovenantModifier, CovenantModifierKind


@dataclass(frozen=True, slots=True)
class ModifierMatch:
    """One semantic recognition event: type + exact source quote fragment(s)."""

    kind: CovenantModifierKind
    detail: str
    reason_code: str
    quotes: tuple[str, ...]


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

    # Rejected reclassifications are excluded.
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

    # EXCLUDE: reclassification into other/non-operating/financial + not counted.
    # Prefer dual-cue detection over brittle single-span distance.
    reclass_m = _search(
        norm,
        r"переквалифицированн\w+|"
        r"переклассифицированн\w+|"
        r"reclassified(?:\s+by\s+(?:the\s+)?auditor|\s+as\b)",
    )
    dest_m = _search(
        norm,
        r"(?:в\s+состав\s+)?(?:финансов\w*|неоперацион\w*)|"
        r"non-?operating|"
        r"financial(?:\s+or\s+other)?(?:\s+items?)?",
    )
    exclude_m = _search(
        norm,
        r"не\s+засчитыва\w*|"
        r"из\s+расчёта\s+исключа\w*|"
        r"исключаются\s+из\s+расчёта|"
        r"shall\s+not\s+be\s+counted|"
        r"not\s+counted|"
        r"excluded\s+from\s+(?:the\s+)?covenant(?:\s+calculation)?|"
        r"disregarded\s+for\s+covenant",
    )
    if reclass_m and dest_m and exclude_m:
        add(
            CovenantModifierKind.AUDITOR_RECLASSIFICATION_EXCLUDE,
            "amounts reclassified into financial/non-operating items are not counted",
            "RECLASS_TO_NONOP_EXCLUDE",
            reclass_m,
            exclude_m,
        )
    else:
        # Compact single-span exclude forms (legacy / short clauses).
        m = _search(
            norm,
            r"переквалифицированн\w*.{0,160}?не\s+засчитыва\w*|"
            r"из\s+расчёта\s+исключается|"
            r"исключаются\s+из\s+расчёта|"
            r"amounts?\s+reclassified.{0,160}?shall\s+not\s+be\s+counted|"
            r"excluded\s+from\s+(?:the\s+)?covenant(?:\s+calculation)?",
        )
        if m:
            add(
                CovenantModifierKind.AUDITOR_RECLASSIFICATION_EXCLUDE,
                "amounts reclassified out of the measured category are excluded",
                "RECLASS_EXCLUDE_SPAN",
                m,
            )

    # INCLUDE: taking into account auditor-accepted / auditor-made reclassifications.
    include_patterns: tuple[tuple[str, str], ...] = (
        (
            r"с\s+учёт[ое]м\s+(?:любой\s+)?переквалификац\w*",
            "INCLUDE_S_UCHETOM_RECLASS",
        ),
        (
            r"переквалификац\w*,?\s+произвед[её]нн\w*\s+аудитор\w*",
            "INCLUDE_AUDITOR_MADE_RECLASS",
        ),
        (
            r"переквалификац\w*,?\s+принят\w*\s+аудитор\w*",
            "INCLUDE_AUDITOR_ACCEPTED_RECLASS",
        ),
        (
            r"включая\s+суммы,\s+переквалифицированн\w+",
            "INCLUDE_INCLUDING_RECLASSIFIED_AMOUNTS",
        ),
        (
            r"переклассифицированн\w*\s+аудитор\w*",
            "INCLUDE_AUDITOR_RECLASSIFIED",
        ),
        (
            r"признают\s+подлежащей\s+отражению",
            "INCLUDE_RECOGNIZE_FOR_REFLECTION",
        ),
        (
            r"корректировк\w*\s+по\s+методу\s+начислен\w*",
            "INCLUDE_ACCRUAL_ADJUSTMENT",
        ),
        (
            r"переквалификац\w*\s+периода",
            "INCLUDE_PERIOD_RECLASS",
        ),
        (
            r"taking\s+into\s+account\s+(?:any\s+)?(?:auditor\s+)?reclassifications?",
            "INCLUDE_EN_TAKING_INTO_ACCOUNT",
        ),
        (
            r"reclassifications?\s+made\s+by\s+(?:the\s+)?auditor",
            "INCLUDE_EN_MADE_BY_AUDITOR",
        ),
        (
            r"reclassified\s+by\s+the\s+auditor",
            "INCLUDE_EN_RECLASSIFIED_BY_AUDITOR",
        ),
        (
            r"auditor-?approved\s+reclassifications?",
            "INCLUDE_EN_AUDITOR_APPROVED",
        ),
        (
            r"subject\s+to\s+(?:auditor\s+)?(?:accrual\s+)?adjustments?",
            "INCLUDE_EN_SUBJECT_TO_ADJUSTMENT",
        ),
        (
            r"accrual\s+adjustments?",
            "INCLUDE_EN_ACCRUAL_ADJUSTMENT",
        ),
        (
            r"period\s+reclassification(?:s)?(?:\s+approved\s+by\s+(?:the\s+)?auditor)?",
            "INCLUDE_EN_PERIOD_RECLASS",
        ),
    )
    for pattern, reason in include_patterns:
        m = _search(norm, pattern)
        if not m:
            continue
        # Guard against non-financial false positives for short accrual/period cues.
        if reason in {
            "INCLUDE_ACCRUAL_ADJUSTMENT",
            "INCLUDE_PERIOD_RECLASS",
            "INCLUDE_EN_ACCRUAL_ADJUSTMENT",
            "INCLUDE_EN_PERIOD_RECLASS",
            "INCLUDE_EN_SUBJECT_TO_ADJUSTMENT",
        }:
            ctx = norm.casefold()
            financial_ctx = any(
                tok in ctx
                for tok in (
                    "аудитор",
                    "ковенант",
                    "auditor",
                    "covenant",
                    "переквалиф",
                    "reclass",
                    "выруч",
                    "revenue",
                    "отчётн",
                    "financ",
                )
            )
            if not financial_ctx:
                continue
            # Reject employee / address / admin noise near the match window.
            window = norm[max(0, m.start() - 40) : m.end() + 40].casefold()
            if any(
                tok in window
                for tok in (
                    "сотрудник",
                    "должност",
                    "адрес",
                    "employee",
                    "department",
                    "administrative",
                )
            ):
                continue
        add(
            CovenantModifierKind.AUDITOR_RECLASSIFICATION_INCLUDE,
            "auditor/accounting reclassifications or adjustments must be reflected",
            reason,
            m,
        )
        break  # one include family match is enough; quotes come from that event

    return found


def extract_modifier_matches(clause_text: str) -> tuple[ModifierMatch, ...]:
    """Return deterministic modifier matches with exact source quote fragments."""
    found = _collect_matches(clause_text)
    # Dedup by kind; keep first match in deterministic scan order.
    seen: set[str] = set()
    out: list[ModifierMatch] = []
    for item in found:
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
