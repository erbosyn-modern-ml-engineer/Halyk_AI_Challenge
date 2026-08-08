"""Data-driven selector source dependencies for TRUE_ZERO gating."""

from __future__ import annotations

from enum import StrEnum

from halyk_agent.domain.covenants.ast import MetricCategory, TransactionSelector
from halyk_agent.domain.fact_extraction.models import (
    FactKind,
    FactRequirementResult,
    RequirementTerminalState,
)


class SelectorSourceDependency(StrEnum):
    LEDGER_ONLY = "LEDGER_ONLY"
    FACT_AUGMENTED = "FACT_AUGMENTED"
    FACT_ONLY = "FACT_ONLY"
    GROUP_SOURCE = "GROUP_SOURCE"
    SUBSIDIARY_STATUS = "SUBSIDIARY_STATUS"
    RELATED_PARTY_IDENTITY = "RELATED_PARTY_IDENTITY"


# Terminal states that prove a fact requirement finished against a trusted source universe.
_TRUSTED_TERMINALS = frozenset(
    {
        RequirementTerminalState.RESOLVED,
        RequirementTerminalState.CONFIRMED_NONE,
        RequirementTerminalState.ABSENT_FROM_SOURCE,
        RequirementTerminalState.NOT_APPLICABLE,
    }
)

# ABSENT reasons that mean the source universe itself is incomplete / untrusted.
_UNTRUSTED_ABSENT_REASONS = frozenset(
    {
        "INCOMPLETE_PPE_ROLL_FORWARD",
        "UNRESOLVED_SOURCE_QUALITY",
        "OCR_REQUIRED",
        "PARTIAL",
        "OCR_FAILED",
        "ENCODING_CORRUPTED",
    }
)

_FACT_KIND_BY_CATEGORY: dict[MetricCategory, tuple[FactKind, ...]] = {
    MetricCategory.ONE_TIME_ADD_BACKS: (FactKind.ONE_TIME_ADD_BACK,),
    MetricCategory.SEVERANCE_LIABILITY: (FactKind.OFF_LEDGER_AMOUNT,),
    MetricCategory.GROUP_CAPEX: (FactKind.GROUP_CAPEX,),
    MetricCategory.CAPITAL_ASSET_TRANSFERS_TO_UNRESTRICTED_SUBS: (FactKind.SUBSIDIARY_STATUS,),
}


def selector_source_dependency(selector: TransactionSelector) -> SelectorSourceDependency:
    """Classify how a selector depends on upstream sources."""
    if selector.related_party_only or selector.category is MetricCategory.RELATED_PARTY_PAYMENTS:
        return SelectorSourceDependency.RELATED_PARTY_IDENTITY
    if selector.category is MetricCategory.GROUP_CAPEX or selector.group_level:
        return SelectorSourceDependency.GROUP_SOURCE
    if selector.category is MetricCategory.CAPITAL_ASSET_TRANSFERS_TO_UNRESTRICTED_SUBS:
        return SelectorSourceDependency.SUBSIDIARY_STATUS
    if selector.category is MetricCategory.ONE_TIME_ADD_BACKS:
        return SelectorSourceDependency.FACT_AUGMENTED
    if selector.category is MetricCategory.SEVERANCE_LIABILITY:
        return SelectorSourceDependency.FACT_ONLY
    return SelectorSourceDependency.LEDGER_ONLY


def fact_kinds_for_selector(selector: TransactionSelector) -> tuple[FactKind, ...]:
    return _FACT_KIND_BY_CATEGORY.get(selector.category, ())


def fact_source_complete_for_selector(
    *,
    scenario_id: str,
    selector: TransactionSelector,
    requirement_results: tuple[FactRequirementResult, ...] | None,
) -> bool:
    """
    Return True when fact/authority source quality is sufficient to assert TRUE_ZERO.

    Fail closed when requirement results are missing for fact-dependent selectors.
    """
    dep = selector_source_dependency(selector)
    if dep is SelectorSourceDependency.LEDGER_ONLY:
        return True
    if dep is SelectorSourceDependency.RELATED_PARTY_IDENTITY:
        # Identity completeness is handled separately via UNKNOWN counters.
        return True
    if requirement_results is None:
        return False

    kinds = fact_kinds_for_selector(selector)
    if not kinds:
        return False

    relevant = [
        r for r in requirement_results if r.scenario_id == scenario_id and r.fact_kind in kinds
    ]
    if not relevant:
        return False

    for result in relevant:
        if result.terminal_state not in _TRUSTED_TERMINALS:
            return False
        if (
            result.terminal_state is RequirementTerminalState.ABSENT_FROM_SOURCE
            and result.reason_code in _UNTRUSTED_ABSENT_REASONS
        ):
            return False
    return True
