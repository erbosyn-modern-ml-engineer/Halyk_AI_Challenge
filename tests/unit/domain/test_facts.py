"""Fact model invariant tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from halyk_agent.domain.evidence import EvidenceSpan
from halyk_agent.domain.facts import DerivedFact, ExplicitFact


@pytest.fixture
def span() -> EvidenceSpan:
    return EvidenceSpan(
        id="span-1",
        source_file="a.pdf",
        document_id="doc-1",
        document_version_id="ver-1",
        page_number=1,
        quote="rate is 12%",
    )


def test_explicit_fact_rejects_empty_evidence(span: EvidenceSpan) -> None:
    with pytest.raises(ValidationError):
        ExplicitFact(
            id="f-1",
            fact_type="interest_rate",
            subject_id="loan-1",
            value="12%",
            evidence=[],
        )


def test_explicit_fact_requires_evidence(span: EvidenceSpan) -> None:
    fact = ExplicitFact(
        id="f-1",
        fact_type="interest_rate",
        subject_id="loan-1",
        value="12%",
        evidence=[span],
    )
    assert len(fact.evidence) == 1


def test_derived_fact_rejects_empty_input_fact_ids() -> None:
    with pytest.raises(ValidationError):
        DerivedFact(
            id="d-1",
            fact_type="net_amount",
            subject_id="loan-1",
            value="100",
            input_fact_ids=[],
            derivation="sum(inputs)",
        )


def test_derived_fact_does_not_require_evidence_spans() -> None:
    fact = DerivedFact(
        id="d-1",
        fact_type="net_amount",
        subject_id="loan-1",
        value="100",
        input_fact_ids=["f-1"],
        derivation="passthrough",
    )
    assert fact.input_fact_ids == ["f-1"]
