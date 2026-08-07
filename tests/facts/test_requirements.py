"""Tests for demand-driven FactRequirement derivation."""

from __future__ import annotations

from halyk_agent.domain.authority.models import AuthorityDomain
from halyk_agent.domain.covenants.ast import MetricCategory, TransactionSelector
from halyk_agent.domain.covenants.models import CovenantModifierKind
from halyk_agent.domain.fact_extraction.models import FactKind
from halyk_agent.domain.fact_extraction.requirements import derive_fact_requirements
from tests.facts.helpers import make_decision, make_definition, reclass_modifier


def test_reclass_modifiers_derive_reclass_period_treatment() -> None:
    definitions = (
        make_definition(
            modifiers=(reclass_modifier(CovenantModifierKind.AUDITOR_RECLASSIFICATION_INCLUDE),)
        ),
    )
    decisions = (make_decision(domain=AuthorityDomain.FINANCIAL_ADJUSTMENTS),)
    reqs = derive_fact_requirements(definitions, decisions)
    kinds = {r.fact_kind for r in reqs}
    assert FactKind.TRANSACTION_RECLASSIFICATION in kinds
    assert FactKind.TRANSACTION_PERIOD in kinds
    assert FactKind.TRANSACTION_TREATMENT in kinds


def test_related_party_selector_derives_ownership_and_threshold() -> None:
    definitions = (
        make_definition(
            selectors=(
                TransactionSelector(
                    category=MetricCategory.RELATED_PARTY_PAYMENTS,
                    related_party_only=True,
                ),
            )
        ),
    )
    decisions = (make_decision(domain=AuthorityDomain.KYC_RELATIONSHIPS),)
    reqs = derive_fact_requirements(definitions, decisions)
    kinds = {r.fact_kind for r in reqs}
    assert FactKind.OWNERSHIP in kinds
    assert FactKind.RELATED_PARTY_THRESHOLD in kinds
    assert all(r.authority_domain is AuthorityDomain.KYC_RELATIONSHIPS for r in reqs)


def test_severance_and_add_back_and_group() -> None:
    definitions = (
        make_definition(
            clause_id="a",
            selectors=(TransactionSelector(category=MetricCategory.SEVERANCE_LIABILITY),),
        ),
        make_definition(
            clause_id="b",
            selectors=(TransactionSelector(category=MetricCategory.ONE_TIME_ADD_BACKS),),
        ),
        make_definition(
            clause_id="c",
            selectors=(
                TransactionSelector(
                    category=MetricCategory.GROUP_CAPEX,
                    group_level=True,
                ),
            ),
        ),
    )
    decisions = (
        make_decision(domain=AuthorityDomain.FINANCIAL_ADJUSTMENTS),
        make_decision(domain=AuthorityDomain.GROUP_STRUCTURE),
    )
    reqs = derive_fact_requirements(definitions, decisions)
    kinds = {r.fact_kind for r in reqs}
    assert FactKind.OFF_LEDGER_AMOUNT in kinds
    assert FactKind.ONE_TIME_ADD_BACK in kinds
    assert FactKind.SUBSIDIARY_STATUS in kinds


def test_no_domain_authority_skips_requirements() -> None:
    definitions = (
        make_definition(
            modifiers=(reclass_modifier(),),
        ),
    )
    decisions = ()  # no AUTHORITATIVE domains
    reqs = derive_fact_requirements(definitions, decisions)
    assert reqs == ()
