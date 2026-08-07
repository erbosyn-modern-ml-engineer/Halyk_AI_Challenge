"""Selector coverage and eligibility helpers for Stage 5D TransactionSelector."""

from __future__ import annotations

from halyk_agent.domain.covenants.ast import MetricCategory, TransactionSelector
from halyk_agent.domain.covenants.models import CovenantDefinition
from halyk_agent.domain.transaction_taxonomy.models import (
    CalculationInput,
    RelatedPartyStatus,
    SelectorCoverageEntry,
)


def _selector_supported(selector: TransactionSelector) -> tuple[bool, str]:
    """All current Stage 5D selector fields are representable in Stage 5F inputs."""
    # include/exclude flags are preserved for Stage 6; empty on public corpus.
    _ = selector.include_flags
    _ = selector.exclude_flags
    try:
        MetricCategory(selector.category)
    except ValueError:
        return False, "UNKNOWN_CATEGORY"
    return True, "OK"


def input_matches_selector(inp: CalculationInput, selector: TransactionSelector) -> bool:
    """
    Exact eligibility using Stage 5F normalized attributes.

    related_party_only never treats UNKNOWN as TRUE.
    RELATED_PARTY_PAYMENTS matches related_party==TRUE payments (outflows preferred).
    """
    if selector.related_party_only and inp.related_party is not RelatedPartyStatus.TRUE:
        return False
    if (
        selector.group_level
        and inp.entity_scope.value not in {"GROUP", "SUBSIDIARY"}
        and "GROUP_SCOPE" not in inp.flags
    ):
        return False

    if selector.category is MetricCategory.RELATED_PARTY_PAYMENTS:
        return inp.related_party is RelatedPartyStatus.TRUE and (
            inp.category is MetricCategory.RELATED_PARTY_PAYMENTS
            or inp.amount < 0
            or inp.category
            in {
                MetricCategory.OPEX,
                MetricCategory.LABOR,
                MetricCategory.RENT,
                MetricCategory.LEASE_PAYMENTS,
                MetricCategory.INSURANCE_PREMIUMS,
                MetricCategory.UTILITIES,
                MetricCategory.TAXES,
                MetricCategory.INTEREST_EXPENSE,
                MetricCategory.CAPEX,
            }
        )

    if selector.category is MetricCategory.GROUP_CAPEX:
        return inp.category in {MetricCategory.GROUP_CAPEX, MetricCategory.CAPEX} and (
            selector.group_level is False
            or inp.entity_scope.value in {"GROUP", "SUBSIDIARY"}
            or "GROUP_SCOPE" in inp.flags
        )

    return inp.category is selector.category


def build_selector_coverage(
    definitions: tuple[CovenantDefinition, ...],
    inputs: tuple[CalculationInput, ...],
) -> tuple[SelectorCoverageEntry, ...]:
    entries: list[SelectorCoverageEntry] = []
    for definition in sorted(definitions, key=lambda d: (d.scenario_id, d.definition_id)):
        for selector in definition.selectors:
            ok, reason = _selector_supported(selector)
            scenario_inputs = [i for i in inputs if i.scenario_id == definition.scenario_id]
            match_count = (
                sum(1 for i in scenario_inputs if input_matches_selector(i, selector)) if ok else 0
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
                    status="SUPPORTED" if ok else "UNSUPPORTED_SELECTOR",
                    reason=reason,
                    matching_input_count=match_count,
                )
            )
    entries.sort(
        key=lambda e: (e.scenario_id, e.definition_id, e.category.value, e.related_party_only)
    )
    return tuple(entries)
