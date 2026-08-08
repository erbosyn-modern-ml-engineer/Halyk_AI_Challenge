"""Derive FactRequirements from Stage 5D covenant definitions + authoritative sources."""

from __future__ import annotations

import re
from collections.abc import Sequence

from halyk_agent.domain.authority.models import AuthorityDecision, AuthorityDomain, AuthorityStatus
from halyk_agent.domain.covenants.ast import MetricCategory
from halyk_agent.domain.covenants.models import CovenantDefinition, CovenantModifierKind
from halyk_agent.domain.fact_extraction.constants import FACT_REQUIREMENT_VERSION
from halyk_agent.domain.fact_extraction.models import DerivationKind, FactKind, FactRequirement
from halyk_agent.domain.ids import deterministic_id
from halyk_agent.domain.parsing import CanonicalDocument

# Concrete FX EVENT cues only — generic accounting-policy Note 5 must NOT trigger.
_FX_EVENT_CUES = (
    "обменный курс составил",
    "обменный курс равен",
    "exchange rate of",
    "exchange rate is",
    "урегулирован в размере",
    "settled in",
    "settled at",
    "settled for",
)
_FX_EVENT_PATTERNS = (
    re.compile(r"(?:курс|exchange\s+rate)\s+(?:равен|составил|of|is)\s+\d", re.IGNORECASE),
    re.compile(
        r"(?:счёт|invoice|amount).{0,100}?(?:EUR|GBP|KZT).{0,140}?(?:урегулирован|settled)",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"(?:урегулирован|settled).{0,80}?(?:\$|USD)",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"TXN-[A-Za-z0-9]+-\d+[^\n]{0,200}?(?:EUR|GBP)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:EUR|GBP)\s*[\d,]+\d.{0,100}?(?:урегулирован|settled|USD|\$)",
        re.IGNORECASE | re.DOTALL,
    ),
)

_AMOUNT_STRONG = (
    "уточнённая сумма",
    "уточненная сумма",
    "исправленная сумма",
    "corrected amount",
    "ledger amount should read",
    "сумма не отражена",
    "фактическая сумма",
    "actual amount",
)

_PERIOD_STRONG = (
    "cutoff",
    "service period",
    "исключена из периода",
    "исключен из периода",
    "исключены из периода",
    "исключена из ковенантного периода",
    "исключен из ковенантного периода",
    "excluded from the covenant period",
    "excluded from covenant period",
    "относится к услугам",
    "оказанным в период",
    "another year",
    "assigned to the covenant period",
    "assign to period",
)

_TREATMENT_STRONG = (
    "из расчёта ковенант",
    "из расчета ковенант",
    "from the covenant calculation",
    "in the covenant calculation",
    "для целей расчёта ковенант",
    "для целей расчета ковенант",
    "exclude from covenant",
    "include in covenant",
)

_RECLASS_CUES = (
    "перекласс",
    "переквалиф",
    "reclass",
    "TXN-",
    "отклон",
    "принят",
)
_RECLASS_STRONG = (
    "перекласс",
    "переквалиф",
    "reclass",
    "переклассификац",
)


def _authoritative_doc_ids(
    decisions: tuple[AuthorityDecision, ...],
    *,
    scenario_id: str,
    domains: Sequence[AuthorityDomain],
) -> set[str]:
    wanted = set(domains)
    out: set[str] = set()
    for decision in decisions:
        if decision.scenario_id != scenario_id:
            continue
        if decision.status is not AuthorityStatus.AUTHORITATIVE:
            continue
        if decision.domain not in wanted:
            continue
        out.update(decision.winning_document_ids)
    return out


def _doc_corpus(
    documents: Sequence[CanonicalDocument],
    doc_ids: set[str],
) -> str:
    parts: list[str] = []
    by_id = {d.document_id: d for d in documents}
    for doc_id in sorted(doc_ids):
        doc = by_id.get(doc_id)
        if doc is None:
            continue
        for page in doc.pages:
            parts.append(page.raw_text or "")
    return "\n".join(parts)


def _has_strong_cues(
    corpus: str, cues: Sequence[str], patterns: Sequence[re.Pattern[str]] = ()
) -> bool:
    if not corpus.strip():
        return False
    lowered = corpus.casefold()
    if any(cue.casefold() in lowered for cue in cues if cue):
        return True
    return any(p.search(corpus) for p in patterns)


def derive_fact_requirements(
    definitions: tuple[CovenantDefinition, ...],
    decisions: tuple[AuthorityDecision, ...],
    documents: tuple[CanonicalDocument, ...] | None = None,
) -> tuple[FactRequirement, ...]:
    """
    Two-phase demand-driven requirements.

    1) SEMANTIC_REQUIRED from covenants alone (never gated on authority existing).
    2) SOURCE_TRIGGERED_CONDITIONAL from strong cues in Stage 5C winning docs.

    SPECULATIVE requirements are forbidden (count must be 0).
    """
    by_scenario: dict[str, list[CovenantDefinition]] = {}
    for definition in definitions:
        by_scenario.setdefault(definition.scenario_id, []).append(definition)

    docs = documents or ()
    requirements: list[FactRequirement] = []

    for scenario_id, defs in sorted(by_scenario.items()):
        clause_ids = tuple(sorted({d.clause_id for d in defs}))
        def_ids = tuple(sorted({d.definition_id for d in defs}))
        modifiers = {m.kind for d in defs for m in d.modifiers}
        categories = {s.category for d in defs for s in d.selectors}
        related_party = any(s.related_party_only for d in defs for s in d.selectors)
        group_level = any(s.group_level for d in defs for s in d.selectors)
        unrestricted = MetricCategory.CAPITAL_ASSET_TRANSFERS_TO_UNRESTRICTED_SUBS in categories
        add_backs = MetricCategory.ONE_TIME_ADD_BACKS in categories
        severance = MetricCategory.SEVERANCE_LIABILITY in categories

        def add(
            *,
            kind: FactKind,
            derivation: DerivationKind,
            domains: tuple[AuthorityDomain, ...],
            reason: str,
            trigger: str,
            cues: tuple[str, ...] = (),
            strong: tuple[str, ...] = (),
            mods: tuple[str, ...] = (),
            cats: tuple[str, ...] = (),
            _scenario_id: str = scenario_id,
            _clause_ids: tuple[str, ...] = clause_ids,
            _def_ids: tuple[str, ...] = def_ids,
        ) -> None:
            requirements.append(
                FactRequirement(
                    requirement_id=deterministic_id(
                        FACT_REQUIREMENT_VERSION,
                        _scenario_id,
                        kind.value,
                        derivation.value,
                        reason,
                        "|".join(d.value for d in domains),
                        trigger,
                    ),
                    scenario_id=_scenario_id,
                    fact_kind=kind,
                    derivation_kind=derivation,
                    trigger_rule=trigger,
                    allowed_authority_domains=domains,
                    upstream_definition_ids=_def_ids,
                    clause_ids=_clause_ids,
                    modifier_kinds=mods,
                    selector_categories=cats,
                    reason_code=reason,
                    lexical_cues=cues,
                    strong_lexical_cues=strong or cues,
                )
            )

        reclass_mods = tuple(
            sorted(
                m.value
                for m in modifiers
                if m
                in {
                    CovenantModifierKind.AUDITOR_RECLASSIFICATION_INCLUDE,
                    CovenantModifierKind.AUDITOR_RECLASSIFICATION_EXCLUDE,
                    CovenantModifierKind.REJECTED_RECLASSIFICATION_EXCLUDE,
                    CovenantModifierKind.BOTH_NUMERATOR_AND_DENOMINATOR_RECLASS,
                }
            )
        )
        # --- Phase 1: SEMANTIC_REQUIRED (never drop for missing authority) ---
        if reclass_mods or CovenantModifierKind.MATERIALITY_FLOOR in modifiers:
            add(
                kind=FactKind.TRANSACTION_RECLASSIFICATION,
                derivation=DerivationKind.SEMANTIC_REQUIRED,
                domains=(AuthorityDomain.FINANCIAL_ADJUSTMENTS,),
                reason="COVENANT_RECLASS_MODIFIER",
                trigger="auditor_or_materiality_reclass_modifier",
                cues=_RECLASS_CUES,
                strong=_RECLASS_STRONG,
                mods=reclass_mods
                or (
                    (CovenantModifierKind.MATERIALITY_FLOOR.value,)
                    if CovenantModifierKind.MATERIALITY_FLOOR in modifiers
                    else ()
                ),
            )

        if related_party or MetricCategory.RELATED_PARTY_PAYMENTS in categories:
            add(
                kind=FactKind.OWNERSHIP,
                derivation=DerivationKind.SEMANTIC_REQUIRED,
                domains=(AuthorityDomain.KYC_RELATIONSHIPS,),
                reason="RELATED_PARTY_OWNERSHIP",
                trigger="related_party_selector_or_category",
                cues=("владе", "ownership", "голосующ", "%", "beneficiar"),
                strong=("владе", "ownership", "голосующ", "бенефициар"),
                cats=("RELATED_PARTY_PAYMENTS",),
            )
            add(
                kind=FactKind.RELATED_PARTY_THRESHOLD,
                derivation=DerivationKind.SEMANTIC_REQUIRED,
                domains=(AuthorityDomain.KYC_RELATIONSHIPS,),
                reason="RELATED_PARTY_THRESHOLD",
                trigger="related_party_selector_or_category",
                cues=("связанн", "related", "владеет", "%"),
                strong=("связанн", "related party", "related-party"),
                cats=("RELATED_PARTY_PAYMENTS",),
            )

        if group_level or unrestricted:
            add(
                kind=FactKind.SUBSIDIARY_STATUS,
                derivation=DerivationKind.SEMANTIC_REQUIRED,
                domains=(AuthorityDomain.GROUP_STRUCTURE, AuthorityDomain.KYC_RELATIONSHIPS),
                reason="GROUP_OR_SUBSIDIARY_STATUS",
                trigger="group_level_or_unrestricted_subs_transfer",
                cues=(
                    "subsidiary",
                    "дочерн",
                    "unrestricted",
                    "restricted",
                    "неограниченн",
                    "ограниченн",
                    "периметр",
                    "залог",
                    "pledged",
                ),
                # Bare "групп"/"group" is NOT enough for model eligibility.
                strong=(
                    "subsidiary",
                    "дочерн",
                    "unrestricted",
                    "restricted",
                    "неограниченн",
                    "ограниченн",
                    "периметр обеспечения",
                    "pledged",
                ),
                cats=tuple(
                    sorted(
                        c.value
                        for c in categories
                        if c
                        in {
                            MetricCategory.GROUP_CAPEX,
                            MetricCategory.CAPITAL_ASSET_TRANSFERS_TO_UNRESTRICTED_SUBS,
                        }
                    )
                ),
            )

        if MetricCategory.GROUP_CAPEX in categories:
            add(
                kind=FactKind.GROUP_CAPEX,
                derivation=DerivationKind.SEMANTIC_REQUIRED,
                domains=(AuthorityDomain.GROUP_STRUCTURE, AuthorityDomain.FINANCIAL_ADJUSTMENTS),
                reason="GROUP_CAPEX_AMOUNT",
                trigger="group_capex_selector",
                cues=(
                    "property, plant and equipment",
                    "ppe",
                    "net book value",
                    "additions",
                    "capex",
                    "capital expenditure",
                    "амортизац",
                ),
                # Only closed-bridge / explicit-additions language is model-eligible.
                # Opening+depreciation+closing alone is incomplete and must stay ABSENT.
                strong=(
                    "additions",
                    "no other movements",
                    "there were no disposals",
                    "there were no transfers",
                    "group capex",
                ),
                cats=("GROUP_CAPEX",),
            )

        if add_backs:
            add(
                kind=FactKind.ONE_TIME_ADD_BACK,
                derivation=DerivationKind.SEMANTIC_REQUIRED,
                domains=(AuthorityDomain.FINANCIAL_ADJUSTMENTS,),
                reason="ONE_TIME_ADD_BACK",
                trigger="one_time_add_backs_category",
                cues=(
                    "one-time",
                    "единовременн",
                    "разов",
                    "add-back",
                    "add back",
                    "скорректированн",
                    "корректировк",
                ),
                strong=(
                    "one-time",
                    "единовременн",
                    "разовые статьи",
                    "разовыми",
                    "add-back",
                    "add back",
                    "корректировки ebitda",
                ),
                cats=("ONE_TIME_ADD_BACKS",),
            )

        if severance:
            add(
                kind=FactKind.OFF_LEDGER_AMOUNT,
                derivation=DerivationKind.SEMANTIC_REQUIRED,
                domains=(AuthorityDomain.FINANCIAL_ADJUSTMENTS,),
                reason="SEVERANCE_OFF_LEDGER",
                trigger="severance_liability_category",
                cues=("выходн", "severance", "пособи"),
                strong=("выходн", "severance"),
                cats=("SEVERANCE_LIABILITY",),
            )

        # --- Phase 2: SOURCE_TRIGGERED_CONDITIONAL (strong cues in winning docs) ---
        fin_docs = _authoritative_doc_ids(
            decisions,
            scenario_id=scenario_id,
            domains=(AuthorityDomain.FINANCIAL_ADJUSTMENTS, AuthorityDomain.TREASURY_FACTS),
        )
        corpus = _doc_corpus(docs, fin_docs) if fin_docs else ""

        if corpus and _has_strong_cues(corpus, _FX_EVENT_CUES, _FX_EVENT_PATTERNS):
            add(
                kind=FactKind.FX_RATE,
                derivation=DerivationKind.SOURCE_TRIGGERED_CONDITIONAL,
                domains=(AuthorityDomain.FINANCIAL_ADJUSTMENTS, AuthorityDomain.TREASURY_FACTS),
                reason="SOURCE_FX_EVENT_CUE",
                trigger="concrete_fx_event_cue_in_winning_doc",
                cues=_FX_EVENT_CUES,
                strong=_FX_EVENT_CUES,
            )

        if corpus and _has_strong_cues(corpus, _AMOUNT_STRONG):
            add(
                kind=FactKind.AMOUNT_CORRECTION,
                derivation=DerivationKind.SOURCE_TRIGGERED_CONDITIONAL,
                domains=(AuthorityDomain.FINANCIAL_ADJUSTMENTS, AuthorityDomain.TREASURY_FACTS),
                reason="SOURCE_AMOUNT_CORRECTION_CUE",
                trigger="strong_amount_correction_cue_in_winning_doc",
                cues=_AMOUNT_STRONG,
                strong=_AMOUNT_STRONG,
            )

        if corpus and _has_strong_cues(corpus, _PERIOD_STRONG):
            add(
                kind=FactKind.TRANSACTION_PERIOD,
                derivation=DerivationKind.SOURCE_TRIGGERED_CONDITIONAL,
                domains=(AuthorityDomain.FINANCIAL_ADJUSTMENTS,),
                reason="SOURCE_PERIOD_CUE",
                trigger="strong_period_cue_in_winning_doc",
                cues=(*_PERIOD_STRONG, "TXN-"),
                strong=_PERIOD_STRONG,
                mods=reclass_mods,
            )

        if corpus and _has_strong_cues(corpus, _TREATMENT_STRONG):
            add(
                kind=FactKind.TRANSACTION_TREATMENT,
                derivation=DerivationKind.SOURCE_TRIGGERED_CONDITIONAL,
                domains=(AuthorityDomain.FINANCIAL_ADJUSTMENTS,),
                reason="SOURCE_TREATMENT_CUE",
                trigger="strong_covenant_treatment_cue_in_winning_doc",
                cues=(*_TREATMENT_STRONG, "TXN-"),
                strong=_TREATMENT_STRONG,
                mods=reclass_mods,
            )

    requirements.sort(
        key=lambda item: (
            item.scenario_id,
            item.fact_kind.value,
            item.derivation_kind.value,
            item.reason_code,
        )
    )
    seen: set[str] = set()
    out: list[FactRequirement] = []
    for item in requirements:
        if item.requirement_id in seen:
            continue
        seen.add(item.requirement_id)
        out.append(item)
    return tuple(out)
