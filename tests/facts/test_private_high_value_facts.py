"""Targeted coverage for private high-value Stage 5E/5F fact inputs."""

from __future__ import annotations

from decimal import Decimal

from halyk_agent.domain.authority.models import AuthorityDomain
from halyk_agent.domain.covenants.ast import MetricCategory
from halyk_agent.domain.fact_extraction.extractors import (
    _normalize_period_label,
    extract_contingent_obligation,
    extract_group_financial_metric,
    extract_scheduled_principal,
)
from halyk_agent.domain.fact_extraction.models import (
    ContingentObligationType,
    DerivationKind,
    FactKind,
    FactRequirement,
    FinancialScopeKind,
    GroupMetricKind,
)
from halyk_agent.domain.parsing import (
    BlockKind,
    CanonicalBlock,
    CanonicalDocument,
    CanonicalPage,
    ParserIdentity,
    ParserKind,
    ParseStatus,
    block_identity,
    compute_metrics,
    document_identity,
)
from halyk_agent.domain.transaction_taxonomy.category_labels import (
    expense_flag_from_label,
    expense_flag_from_rule,
)
from halyk_agent.domain.transaction_taxonomy.classify import classify_description


def _doc(text: str) -> CanonicalDocument:
    digest = "c" * 64
    artifact = "private-hv"
    doc_id = document_identity(artifact, digest)
    block = CanonicalBlock(
        id=block_identity(doc_id, 1, 0, BlockKind.PAGE_TEXT, text, None),
        page_number=1,
        ordinal=0,
        kind=BlockKind.PAGE_TEXT,
        raw_text=text,
        normalized_text=text,
        char_start=0,
        char_end=len(text),
        source_parser=ParserKind.PYPDF,
        metadata={},
    )
    page = CanonicalPage(
        page_number=1,
        raw_text=text,
        normalized_text=text,
        blocks=[block],
    )
    return CanonicalDocument(
        id=doc_id,
        artifact_id=artifact,
        document_id=doc_id,
        document_version_id="v1",
        source_file="private.pdf",
        source_sha256=digest,
        parser=ParserIdentity(
            kind=ParserKind.PYPDF,
            package_name="pypdf",
            package_version="1",
            configuration_hash="c",
        ),
        status=ParseStatus.SUCCESS,
        pages=[page],
        metrics=compute_metrics([page]),
    )


def _req(kind: FactKind) -> FactRequirement:
    return FactRequirement(
        requirement_id=f"req-{kind.value}",
        scenario_id="KC",
        fact_kind=kind,
        derivation_kind=DerivationKind.SOURCE_TRIGGERED_CONDITIONAL,
        trigger_rule="test",
        allowed_authority_domains=(AuthorityDomain.FINANCIAL_ADJUSTMENTS,),
        reason_code="TEST",
        lexical_cues=(),
        strong_lexical_cues=(),
    )


def test_normalize_period_label_quarters() -> None:
    assert _normalize_period_label("Q2 2025 marketing") == "2025-Q2"
    assert _normalize_period_label("2 квартал") == "Q2"


def test_classify_marketing_and_consulting_distinct_flags() -> None:
    m = classify_description("National marketing campaign media buy")
    assert m.status == "CLASSIFIED"
    assert m.category is MetricCategory.OTHER_EXPENSE
    assert m.rule == "MARKETING"
    assert expense_flag_from_rule(m.rule) == "MARKETING"

    c = classify_description("Management consulting advisory retainer")
    assert c.status == "CLASSIFIED"
    assert c.category is MetricCategory.OTHER_EXPENSE
    assert c.rule == "CONSULTING"
    assert expense_flag_from_label("consulting fees") == "CONSULTING"

    p = classify_description("Scheduled principal repayment under term loan")
    assert p.status == "CLASSIFIED"
    assert p.category is MetricCategory.OTHER_EXPENSE
    assert p.rule == "SCHEDULED_PRINCIPAL"
    assert p.category is not MetricCategory.FINANCING_INFLOWS


def test_extract_group_financial_metric() -> None:
    text = "Group EBITDA for FY2025 was USD 1,250,000.00 according to the auditor."
    cands = extract_group_financial_metric(
        _req(FactKind.GROUP_FINANCIAL_METRIC),
        _doc(text),
        AuthorityDomain.GROUP_STRUCTURE,
    )
    assert len(cands) == 1
    payload = cands[0].payload
    assert payload.metric is GroupMetricKind.EBITDA
    assert payload.scope is FinancialScopeKind.GROUP
    assert payload.amount.value == Decimal("1250000.00")
    assert payload.amount.currency == "USD"


def test_extract_contingent_obligation_guarantee() -> None:
    text = "Guarantee outstanding of $250,000.00 as of year end."
    cands = extract_contingent_obligation(
        _req(FactKind.CONTINGENT_OBLIGATION),
        _doc(text),
        AuthorityDomain.FINANCIAL_ADJUSTMENTS,
    )
    assert len(cands) == 1
    payload = cands[0].payload
    assert payload.obligation_type is ContingentObligationType.GUARANTEE
    assert payload.amount.value == Decimal("250000.00")


def test_extract_scheduled_principal_not_financing() -> None:
    text = "Scheduled principal repayments of $500,000.00 for 2025."
    cands = extract_scheduled_principal(
        _req(FactKind.SCHEDULED_PRINCIPAL),
        _doc(text),
        AuthorityDomain.TREASURY_FACTS,
    )
    assert len(cands) == 1
    payload = cands[0].payload
    assert payload.amount.value == Decimal("500000.00")
    assert payload.description == "scheduled_principal_repayment"
