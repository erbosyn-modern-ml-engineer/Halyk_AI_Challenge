"""Derive FactRequirements from Stage 5D covenant definitions + authority."""

from __future__ import annotations

from halyk_agent.domain.authority.models import AuthorityDecision, AuthorityDomain, AuthorityStatus
from halyk_agent.domain.covenants.ast import MetricCategory
from halyk_agent.domain.covenants.models import CovenantDefinition, CovenantModifierKind
from halyk_agent.domain.fact_extraction.constants import FACT_REQUIREMENT_VERSION
from halyk_agent.domain.fact_extraction.models import FactKind, FactRequirement
from halyk_agent.domain.ids import deterministic_id


def _authoritative_domains(
    decisions: tuple[AuthorityDecision, ...],
    *,
    scenario_id: str,
) -> set[AuthorityDomain]:
    out: set[AuthorityDomain] = set()
    for decision in decisions:
        if decision.scenario_id != scenario_id:
            continue
        if decision.status is not AuthorityStatus.AUTHORITATIVE:
            continue
        out.add(decision.domain)
    return out


def derive_fact_requirements(
    definitions: tuple[CovenantDefinition, ...],
    decisions: tuple[AuthorityDecision, ...],
) -> tuple[FactRequirement, ...]:
    """
    Demand-driven requirements: only ask for facts Stage 5D semantics need.

    Does not extract facts and does not mutate ledgers.
    """
    by_scenario: dict[str, list[CovenantDefinition]] = {}
    for definition in definitions:
        by_scenario.setdefault(definition.scenario_id, []).append(definition)

    requirements: list[FactRequirement] = []
    for scenario_id, defs in sorted(by_scenario.items()):
        domains = _authoritative_domains(decisions, scenario_id=scenario_id)
        clause_ids = tuple(sorted({d.clause_id for d in defs}))
        modifiers = {m.kind for d in defs for m in d.modifiers}
        categories = {s.category for d in defs for s in d.selectors}
        related_party = any(s.related_party_only for d in defs for s in d.selectors)
        group_level = any(s.group_level for d in defs for s in d.selectors)
        unrestricted = MetricCategory.CAPITAL_ASSET_TRANSFERS_TO_UNRESTRICTED_SUBS in categories
        add_backs = MetricCategory.ONE_TIME_ADD_BACKS in categories
        severance = MetricCategory.SEVERANCE_LIABILITY in categories

        def add(
            kind: FactKind,
            domain: AuthorityDomain,
            reason: str,
            *,
            cues: tuple[str, ...] = (),
            mods: tuple[str, ...] = (),
            cats: tuple[str, ...] = (),
            _domains: set[AuthorityDomain] = domains,
            _scenario_id: str = scenario_id,
            _clause_ids: tuple[str, ...] = clause_ids,
        ) -> None:
            if domain not in _domains:
                return
            requirements.append(
                FactRequirement(
                    requirement_id=deterministic_id(
                        FACT_REQUIREMENT_VERSION,
                        _scenario_id,
                        kind.value,
                        domain.value,
                        reason,
                    ),
                    scenario_id=_scenario_id,
                    fact_kind=kind,
                    authority_domain=domain,
                    clause_ids=_clause_ids,
                    modifier_kinds=mods,
                    selector_categories=cats,
                    reason_code=reason,
                    lexical_cues=cues,
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
        if reclass_mods or CovenantModifierKind.MATERIALITY_FLOOR in modifiers:
            add(
                FactKind.TRANSACTION_RECLASSIFICATION,
                AuthorityDomain.FINANCIAL_ADJUSTMENTS,
                "COVENANT_RECLASS_MODIFIER",
                cues=(
                    "перекласс",
                    "переквалиф",
                    "reclass",
                    "TXN-",
                    "отклон",
                    "принят",
                ),
                mods=reclass_mods
                or (
                    (CovenantModifierKind.MATERIALITY_FLOOR.value,)
                    if CovenantModifierKind.MATERIALITY_FLOOR in modifiers
                    else ()
                ),
            )
            add(
                FactKind.TRANSACTION_PERIOD,
                AuthorityDomain.FINANCIAL_ADJUSTMENTS,
                "COVENANT_PERIOD_TREATMENT",
                cues=("TXN-", "период", "исключ", "относится", "отсечен", "cutoff"),
                mods=reclass_mods,
            )
            add(
                FactKind.TRANSACTION_TREATMENT,
                AuthorityDomain.FINANCIAL_ADJUSTMENTS,
                "COVENANT_TXN_TREATMENT",
                cues=("TXN-", "включ", "исключ", "include", "exclude"),
                mods=reclass_mods,
            )

        if related_party or MetricCategory.RELATED_PARTY_PAYMENTS in categories:
            add(
                FactKind.OWNERSHIP,
                AuthorityDomain.KYC_RELATIONSHIPS,
                "RELATED_PARTY_OWNERSHIP",
                cues=("владе", "ownership", "голосующ", "%", "beneficial"),
                cats=("RELATED_PARTY_PAYMENTS",),
            )
            add(
                FactKind.RELATED_PARTY_THRESHOLD,
                AuthorityDomain.KYC_RELATIONSHIPS,
                "RELATED_PARTY_THRESHOLD",
                cues=("связанн", "related", "владеет", "%"),
                cats=("RELATED_PARTY_PAYMENTS",),
            )

        if group_level or unrestricted:
            add(
                FactKind.SUBSIDIARY_STATUS,
                AuthorityDomain.GROUP_STRUCTURE,
                "GROUP_OR_SUBSIDIARY_STATUS",
                cues=(
                    "subsidiary",
                    "дочерн",
                    "unrestricted",
                    "restricted",
                    "неограниченн",
                    "ограниченн",
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

        if add_backs:
            add(
                FactKind.ONE_TIME_ADD_BACK,
                AuthorityDomain.FINANCIAL_ADJUSTMENTS,
                "ONE_TIME_ADD_BACK",
                cues=("one-time", "единовременн", "add-back", "add back", "скорректированн"),
                cats=("ONE_TIME_ADD_BACKS",),
            )

        if severance:
            add(
                FactKind.OFF_LEDGER_AMOUNT,
                AuthorityDomain.FINANCIAL_ADJUSTMENTS,
                "SEVERANCE_OFF_LEDGER",
                cues=("выходн", "severance", "пособи"),
                cats=("SEVERANCE_LIABILITY",),
            )

        # FX / amount corrections are useful whenever financial adjustments authority exists
        # and covenant cells depend on money metrics.
        money_cats = any(
            c
            in {
                MetricCategory.REVENUE,
                MetricCategory.CAPEX,
                MetricCategory.OPEX,
                MetricCategory.INTEREST_EXPENSE,
                MetricCategory.FINANCING_INFLOWS,
            }
            for c in categories
        )
        if money_cats and AuthorityDomain.FINANCIAL_ADJUSTMENTS in domains:
            add(
                FactKind.FX_RATE,
                AuthorityDomain.FINANCIAL_ADJUSTMENTS,
                "FX_OR_CURRENCY_TREATMENT",
                cues=("курс", "EUR", "€", "exchange rate", "валют", "FX"),
            )
            add(
                FactKind.AMOUNT_CORRECTION,
                AuthorityDomain.FINANCIAL_ADJUSTMENTS,
                "AMOUNT_CORRECTION",
                cues=("сумма", "amount", "TXN-", "$"),
            )
        if money_cats and AuthorityDomain.TREASURY_FACTS in domains:
            add(
                FactKind.AMOUNT_CORRECTION,
                AuthorityDomain.TREASURY_FACTS,
                "TREASURY_AMOUNT",
                cues=("TXN-", "сумма", "amount", "$", "treasury"),
            )
            add(
                FactKind.FX_RATE,
                AuthorityDomain.TREASURY_FACTS,
                "TREASURY_FX",
                cues=("курс", "EUR", "exchange", "валют"),
            )

    requirements.sort(
        key=lambda item: (item.scenario_id, item.fact_kind.value, item.authority_domain.value)
    )
    # Dedup identical requirement ids (deterministic_id already collapses reason/domain/kind).
    seen: set[str] = set()
    out: list[FactRequirement] = []
    for item in requirements:
        if item.requirement_id in seen:
            continue
        seen.add(item.requirement_id)
        out.append(item)
    return tuple(out)
