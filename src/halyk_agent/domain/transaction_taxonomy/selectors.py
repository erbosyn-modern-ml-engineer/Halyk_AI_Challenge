"""Selector coverage, membership matching, and Stage 6 readiness."""

from __future__ import annotations

from halyk_agent.domain.covenants.ast import MetricCategory, TransactionSelector
from halyk_agent.domain.covenants.models import CovenantDefinition
from halyk_agent.domain.fact_extraction.models import FactRequirementResult
from halyk_agent.domain.transaction_taxonomy.models import (
    CalculationInput,
    DefinitionReadinessEntry,
    RelatedPartyStatus,
    SelectorCoverageEntry,
    SelectorReadinessStatus,
    SubsidiaryStatusKind,
)
from halyk_agent.domain.transaction_taxonomy.selector_dependencies import (
    SelectorSourceDependency,
    fact_source_complete_for_selector,
    selector_source_dependency,
)


def input_matches_selector(inp: CalculationInput, selector: TransactionSelector) -> bool:
    """
    Exact eligibility using Stage 5F normalized attributes + semantic memberships.

    related_party_only never treats UNKNOWN as TRUE.
    GROUP_CAPEX never matches plain borrower CAPEX.
    Unrestricted-sub transfers require subsidiary_status == UNRESTRICTED.
    """
    if selector.related_party_only and inp.related_party is not RelatedPartyStatus.TRUE:
        return False

    memberships = inp.selector_categories or (inp.category,)

    if selector.category is MetricCategory.RELATED_PARTY_PAYMENTS:
        return inp.related_party is RelatedPartyStatus.TRUE and (
            MetricCategory.RELATED_PARTY_PAYMENTS in memberships
            or inp.amount < 0
            or any(
                cat
                in {
                    MetricCategory.OPEX,
                    MetricCategory.OTHER_EXPENSE,
                    MetricCategory.LABOR,
                    MetricCategory.RENT,
                    MetricCategory.LEASE_PAYMENTS,
                    MetricCategory.INSURANCE_PREMIUMS,
                    MetricCategory.UTILITIES,
                    MetricCategory.TAXES,
                    MetricCategory.INTEREST_EXPENSE,
                    MetricCategory.CAPEX,
                }
                for cat in memberships
            )
        )

    if selector.category is MetricCategory.GROUP_CAPEX:
        # Only trusted group-level inputs — never borrower CAPEX substitution.
        return (
            MetricCategory.GROUP_CAPEX in memberships
            and inp.entity_scope.value == "GROUP"
            and "GROUP_LEVEL_SOURCE" in inp.flags
        )

    if selector.category is MetricCategory.CAPITAL_ASSET_TRANSFERS_TO_UNRESTRICTED_SUBS:
        return (
            MetricCategory.CAPITAL_ASSET_TRANSFERS_TO_UNRESTRICTED_SUBS in memberships
            and inp.subsidiary_status is SubsidiaryStatusKind.UNRESTRICTED
        )

    if (
        selector.group_level
        and "GROUP_LEVEL_SOURCE" not in inp.flags
        and inp.entity_scope.value not in {"GROUP"}
    ):
        return False

    return selector.category in memberships


def _selector_readiness(
    *,
    definition: CovenantDefinition,
    selector: TransactionSelector,
    match_count: int,
    scenario_inputs: list[CalculationInput],
    group_capex_unresolved: set[str],
    unrestricted_unresolved: set[str],
    requirement_results: tuple[FactRequirementResult, ...] | None,
) -> tuple[SelectorReadinessStatus, str]:
    dep = selector_source_dependency(selector)

    if selector.category is MetricCategory.GROUP_CAPEX:
        if definition.scenario_id in group_capex_unresolved:
            return (
                SelectorReadinessStatus.UNRESOLVED,
                "GROUP_CAPEX_OPERAND_UNRESOLVED",
            )
        if match_count == 0:
            if not fact_source_complete_for_selector(
                scenario_id=definition.scenario_id,
                selector=selector,
                requirement_results=requirement_results,
            ):
                return (
                    SelectorReadinessStatus.UNRESOLVED,
                    "UNRESOLVED_SOURCE_QUALITY",
                )
            return SelectorReadinessStatus.TRUE_ZERO, "NO_GROUP_CAPEX_INPUTS"
        return SelectorReadinessStatus.READY, "OK"

    if selector.category is MetricCategory.CAPITAL_ASSET_TRANSFERS_TO_UNRESTRICTED_SUBS:
        if definition.scenario_id in unrestricted_unresolved:
            return (
                SelectorReadinessStatus.UNRESOLVED,
                "UNRESTRICTED_SUBSIDIARY_STATUS_UNKNOWN",
            )
        if match_count == 0:
            if not fact_source_complete_for_selector(
                scenario_id=definition.scenario_id,
                selector=selector,
                requirement_results=requirement_results,
            ):
                return (
                    SelectorReadinessStatus.UNRESOLVED,
                    "UNRESOLVED_SOURCE_QUALITY",
                )
            return SelectorReadinessStatus.TRUE_ZERO, "NO_UNRESTRICTED_TRANSFER_INPUTS"
        return SelectorReadinessStatus.READY, "OK"

    if selector.related_party_only or dep is SelectorSourceDependency.RELATED_PARTY_IDENTITY:
        unknowns = sum(1 for i in scenario_inputs if i.related_party is RelatedPartyStatus.UNKNOWN)
        if unknowns and match_count == 0:
            return (
                SelectorReadinessStatus.UNRESOLVED,
                "RELATED_PARTY_IDENTITY_UNKNOWN",
            )

    if match_count == 0:
        if dep in {
            SelectorSourceDependency.FACT_ONLY,
            SelectorSourceDependency.FACT_AUGMENTED,
            SelectorSourceDependency.GROUP_SOURCE,
            SelectorSourceDependency.SUBSIDIARY_STATUS,
        } and not fact_source_complete_for_selector(
            scenario_id=definition.scenario_id,
            selector=selector,
            requirement_results=requirement_results,
        ):
            return (
                SelectorReadinessStatus.UNRESOLVED,
                "UNRESOLVED_SOURCE_QUALITY",
            )
        return SelectorReadinessStatus.TRUE_ZERO, "NO_MATCHING_INPUTS"
    return SelectorReadinessStatus.READY, "OK"


def build_selector_coverage(
    definitions: tuple[CovenantDefinition, ...],
    inputs: tuple[CalculationInput, ...],
    *,
    group_capex_unresolved: set[str] | None = None,
    unrestricted_unresolved: set[str] | None = None,
    requirement_results: tuple[FactRequirementResult, ...] | None = None,
) -> tuple[SelectorCoverageEntry, ...]:
    group_unres = group_capex_unresolved or set()
    unres_sub = unrestricted_unresolved or set()
    entries: list[SelectorCoverageEntry] = []
    for definition in sorted(definitions, key=lambda d: (d.scenario_id, d.definition_id)):
        scenario_inputs = [i for i in inputs if i.scenario_id == definition.scenario_id]
        for selector in definition.selectors:
            match_count = sum(1 for i in scenario_inputs if input_matches_selector(i, selector))
            status, reason = _selector_readiness(
                definition=definition,
                selector=selector,
                match_count=match_count,
                scenario_inputs=scenario_inputs,
                group_capex_unresolved=group_unres,
                unrestricted_unresolved=unres_sub,
                requirement_results=requirement_results,
            )
            entries.append(
                SelectorCoverageEntry(
                    definition_id=definition.definition_id,
                    scenario_id=definition.scenario_id,
                    category=selector.category,
                    related_party_only=selector.related_party_only,
                    group_level=selector.group_level,
                    include_flags=selector.include_flags,
                    exclude_flags=selector.exclude_flags,
                    status=status,
                    reason_code=reason,
                    matching_input_count=match_count,
                )
            )
    entries.sort(
        key=lambda e: (e.scenario_id, e.definition_id, e.category.value, e.related_party_only)
    )
    return tuple(entries)


def build_definition_readiness(
    definitions: tuple[CovenantDefinition, ...],
    selector_coverage: tuple[SelectorCoverageEntry, ...],
) -> tuple[DefinitionReadinessEntry, ...]:
    by_def: dict[str, list[SelectorCoverageEntry]] = {}
    for entry in selector_coverage:
        by_def.setdefault(entry.definition_id, []).append(entry)

    out: list[DefinitionReadinessEntry] = []
    for definition in sorted(definitions, key=lambda d: (d.scenario_id, d.definition_id)):
        entries = by_def.get(definition.definition_id, [])
        unresolved = [
            f"{e.category.value}:{e.reason_code}"
            for e in entries
            if e.status is SelectorReadinessStatus.UNRESOLVED
        ]
        if unresolved:
            out.append(
                DefinitionReadinessEntry(
                    definition_id=definition.definition_id,
                    scenario_id=definition.scenario_id,
                    status=SelectorReadinessStatus.UNRESOLVED,
                    reason_code="UNRESOLVED_INPUT",
                    unresolved_selectors=tuple(unresolved),
                )
            )
        else:
            out.append(
                DefinitionReadinessEntry(
                    definition_id=definition.definition_id,
                    scenario_id=definition.scenario_id,
                    status=SelectorReadinessStatus.READY,
                    reason_code="OK",
                )
            )
    return tuple(out)
