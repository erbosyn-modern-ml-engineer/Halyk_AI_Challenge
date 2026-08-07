"""Extract covenant-side modifiers retained for later authoritative fact stages."""

from __future__ import annotations

import re

from halyk_agent.domain.covenants.models import CovenantModifier, CovenantModifierKind


def extract_modifiers(clause_text: str) -> tuple[CovenantModifier, ...]:
    text = " ".join(clause_text.replace("\xa0", " ").split())
    low = text.casefold()
    found: list[CovenantModifier] = []

    def add(kind: CovenantModifierKind, detail: str, phrase: str) -> None:
        found.append(CovenantModifier(kind=kind, detail=detail, evidence_span_ids=()))

    if re.search(r"порог\w*\s+существенност|materialit", low):
        add(
            CovenantModifierKind.MATERIALITY_FLOOR,
            "materiality floor for add-backs / adjustments",
            "существенности",
        )
    if re.search(
        r"переклассифицированн\w*.{0,80}?числител.{0,40}?знаменател|"
        r"как в числителе, так и в знаменателе",
        low,
        re.DOTALL,
    ):
        add(
            CovenantModifierKind.BOTH_NUMERATOR_AND_DENOMINATOR_RECLASS,
            "auditor reclassifications apply to numerator and denominator",
            "числителе",
        )
    if re.search(
        r"переквалифицированн\w*.{0,60}?не\s+засчитыва|"
        r"из расчёта исключается|"
        r"исключаются из расчёта",
        low,
        re.DOTALL,
    ):
        add(
            CovenantModifierKind.AUDITOR_RECLASSIFICATION_EXCLUDE,
            "amounts reclassified out of the measured category are excluded",
            "исключ",
        )
    if re.search(
        r"включая суммы, переквалифицированные|"
        r"переклассифицированная аудиторами|"
        r"признают подлежащей отражению",
        low,
    ):
        add(
            CovenantModifierKind.AUDITOR_RECLASSIFICATION_INCLUDE,
            "auditor reclassifications into the measured category are included",
            "переквалифицир",
        )
    if re.search(
        r"отклонённ\w*\s+аудитор|"
        r"rejected by the auditor|"
        r"рассмотренные и отклонённые аудиторами",
        low,
    ):
        add(
            CovenantModifierKind.REJECTED_RECLASSIFICATION_EXCLUDE,
            "reclassifications considered and rejected by auditors are excluded",
            "отклонён",
        )

    # Dedup by kind.
    seen: set[str] = set()
    out: list[CovenantModifier] = []
    for item in found:
        if item.kind.value in seen:
            continue
        seen.add(item.kind.value)
        out.append(item)
    return tuple(out)
