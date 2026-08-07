"""Tests for demand-driven FactRequirement derivation."""

from __future__ import annotations

from halyk_agent.domain.authority.models import AuthorityDomain
from halyk_agent.domain.covenants.ast import MetricCategory, TransactionSelector
from halyk_agent.domain.covenants.models import CovenantModifierKind
from halyk_agent.domain.fact_extraction.models import DerivationKind, FactKind
from halyk_agent.domain.fact_extraction.requirements import derive_fact_requirements
from tests.authority.helpers import make_document
from tests.facts.helpers import make_decision, make_definition, reclass_modifier


def test_reclass_modifiers_derive_reclass_only_not_speculative() -> None:
    definitions = (
        make_definition(
            modifiers=(reclass_modifier(CovenantModifierKind.AUDITOR_RECLASSIFICATION_INCLUDE),)
        ),
    )
    decisions = (make_decision(domain=AuthorityDomain.FINANCIAL_ADJUSTMENTS),)
    reqs = derive_fact_requirements(definitions, decisions)
    kinds = {r.fact_kind for r in reqs}
    assert FactKind.TRANSACTION_RECLASSIFICATION in kinds
    # Speculative clones forbidden
    assert FactKind.TRANSACTION_PERIOD not in kinds
    assert FactKind.TRANSACTION_TREATMENT not in kinds
    assert FactKind.FX_RATE not in kinds
    assert FactKind.AMOUNT_CORRECTION not in kinds
    assert all(r.derivation_kind is DerivationKind.SEMANTIC_REQUIRED for r in reqs)


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
    assert all(AuthorityDomain.KYC_RELATIONSHIPS in r.allowed_authority_domains for r in reqs)


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


def test_subsidiary_not_dropped_without_group_structure_authority() -> None:
    definitions = (
        make_definition(
            selectors=(
                TransactionSelector(
                    category=MetricCategory.CAPITAL_ASSET_TRANSFERS_TO_UNRESTRICTED_SUBS,
                    group_level=True,
                ),
            )
        ),
    )
    decisions = ()  # no GROUP_STRUCTURE authority
    reqs = derive_fact_requirements(definitions, decisions)
    sub = [r for r in reqs if r.fact_kind is FactKind.SUBSIDIARY_STATUS]
    assert len(sub) == 1
    assert AuthorityDomain.GROUP_STRUCTURE in sub[0].allowed_authority_domains
    assert AuthorityDomain.KYC_RELATIONSHIPS in sub[0].allowed_authority_domains


def test_no_speculative_fx_from_money_plus_auditor() -> None:
    definitions = (
        make_definition(
            category=MetricCategory.REVENUE,
            modifiers=(reclass_modifier(),),
        ),
    )
    decisions = (make_decision(domain=AuthorityDomain.FINANCIAL_ADJUSTMENTS),)
    # Money doc without FX strong cues
    doc = make_document(raw_text="Revenue $100 USD and auditor note without FX language.")
    reqs = derive_fact_requirements(definitions, decisions, (doc,))
    kinds = {r.fact_kind for r in reqs}
    assert FactKind.FX_RATE not in kinds
    assert FactKind.AMOUNT_CORRECTION not in kinds
    assert FactKind.TRANSACTION_PERIOD not in kinds


def test_source_triggered_fx_and_period_from_strong_cues() -> None:
    definitions = (make_definition(category=MetricCategory.REVENUE),)
    decisions = (make_decision(domain=AuthorityDomain.FINANCIAL_ADJUSTMENTS, winning=("doc-1",)),)
    doc = make_document(
        raw_text=(
            "Invoice на сумму 100 EUR урегулирован в размере $116.00. "
            "TXN-A-1 исключена из периода; exchange rate 1.16 EUR/USD."
        )
    )
    # make_document may use a different document_id — align decision winning id
    decisions = (
        make_decision(
            domain=AuthorityDomain.FINANCIAL_ADJUSTMENTS,
            winning=(doc.document_id,),
        ),
    )
    reqs = derive_fact_requirements(definitions, decisions, (doc,))
    by_kind = {r.fact_kind: r for r in reqs}
    assert FactKind.FX_RATE in by_kind
    assert by_kind[FactKind.FX_RATE].derivation_kind is DerivationKind.SOURCE_TRIGGERED_CONDITIONAL
    assert FactKind.TRANSACTION_PERIOD in by_kind


def test_speculative_count_always_zero() -> None:
    definitions = (
        make_definition(modifiers=(reclass_modifier(),)),
        make_definition(
            clause_id="rp",
            selectors=(
                TransactionSelector(
                    category=MetricCategory.RELATED_PARTY_PAYMENTS,
                    related_party_only=True,
                ),
            ),
        ),
    )
    decisions = (
        make_decision(domain=AuthorityDomain.FINANCIAL_ADJUSTMENTS),
        make_decision(domain=AuthorityDomain.KYC_RELATIONSHIPS),
    )
    reqs = derive_fact_requirements(definitions, decisions)
    assert all(
        r.derivation_kind
        in {DerivationKind.SEMANTIC_REQUIRED, DerivationKind.SOURCE_TRIGGERED_CONDITIONAL}
        for r in reqs
    )
