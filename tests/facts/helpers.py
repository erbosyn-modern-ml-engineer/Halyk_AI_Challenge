"""Helpers for Stage 5E fact extraction tests."""

from __future__ import annotations

from decimal import Decimal

from halyk_agent.domain.authority.models import (
    AuthorityDecision,
    AuthorityDomain,
    AuthorityStatus,
)
from halyk_agent.domain.covenants.ast import MetricCategory, TransactionSelector, money_sum
from halyk_agent.domain.covenants.models import (
    Comparator,
    CovenantDefinition,
    CovenantEvidenceRefs,
    CovenantModifier,
    CovenantModifierKind,
    PeriodDefinition,
    PeriodKind,
    ScopeDefinition,
    ScopeKind,
)
from halyk_agent.domain.covenants.quantity import QuantityType, TypedQuantity
from halyk_agent.domain.fact_extraction.models import (
    DerivationKind,
    FactKind,
    FactRequirement,
)
from halyk_agent.domain.ids import deterministic_id


def make_definition(
    *,
    scenario_id: str = "S1",
    clause_id: str = "6.1",
    modifiers: tuple[CovenantModifier, ...] = (),
    selectors: tuple[TransactionSelector, ...] | None = None,
    category: MetricCategory = MetricCategory.REVENUE,
) -> CovenantDefinition:
    sels = selectors if selectors is not None else (TransactionSelector(category=category),)
    return CovenantDefinition(
        definition_id=deterministic_id("def", scenario_id, clause_id),
        scenario_id=scenario_id,
        clause_id=clause_id,
        document_id="doc-1",
        document_version_id="v1",
        source_file="loan.pdf",
        source_sha256="a" * 64,
        family_id="fam-1",
        metric=money_sum(sels[0].category),
        metric_quantity_type=QuantityType.MONEY,
        comparator=Comparator.LTE,
        threshold=TypedQuantity(
            quantity_type=QuantityType.MONEY,
            value=Decimal("100"),
            currency="USD",
        ),
        period=PeriodDefinition(period_kind=PeriodKind.AS_OF),
        scope=ScopeDefinition(scope_kind=ScopeKind.BORROWER),
        selectors=sels,
        modifiers=modifiers,
        evidence=CovenantEvidenceRefs(),
        rendered="test",
    )


def make_decision(
    *,
    scenario_id: str = "S1",
    domain: AuthorityDomain = AuthorityDomain.FINANCIAL_ADJUSTMENTS,
    status: AuthorityStatus = AuthorityStatus.AUTHORITATIVE,
    winning: tuple[str, ...] = ("doc-1",),
) -> AuthorityDecision:
    return AuthorityDecision(
        decision_id=deterministic_id("dec", scenario_id, domain.value),
        scenario_id=scenario_id,
        domain=domain,
        status=status,
        rule_id="test-rule",
        reason="test",
        winning_document_ids=winning,
    )


def reclass_modifier(
    kind: CovenantModifierKind = CovenantModifierKind.AUDITOR_RECLASSIFICATION_INCLUDE,
) -> CovenantModifier:
    return CovenantModifier(kind=kind, detail="reclass")


def make_requirement(
    kind: FactKind,
    *cues: str,
    domain: AuthorityDomain = AuthorityDomain.FINANCIAL_ADJUSTMENTS,
    domains: tuple[AuthorityDomain, ...] | None = None,
    derivation: DerivationKind = DerivationKind.SEMANTIC_REQUIRED,
    strong: tuple[str, ...] = (),
    reason: str = "TEST",
) -> FactRequirement:
    allowed = domains or (domain,)
    return FactRequirement(
        requirement_id=f"req-{kind.value}",
        scenario_id="S1",
        fact_kind=kind,
        derivation_kind=derivation,
        trigger_rule="test",
        allowed_authority_domains=allowed,
        reason_code=reason,
        lexical_cues=cues,
        strong_lexical_cues=strong or cues,
    )
