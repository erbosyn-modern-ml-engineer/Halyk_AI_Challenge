"""ProofBundle completeness tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from halyk_agent.domain.calculations import CalculatedValue, CalculationTrace
from halyk_agent.domain.decisions import DecisionResult, DecisionStatus
from halyk_agent.domain.documents import (
    ApplicableVersionSet,
    DocumentVersionRef,
    DocumentVersionStatus,
)
from halyk_agent.domain.evidence import EvidenceSpan
from halyk_agent.domain.facts import ExplicitFact
from halyk_agent.domain.proof import ProofBundle
from halyk_agent.domain.rules import RuleRef


def _version() -> DocumentVersionRef:
    return DocumentVersionRef(
        document_id="doc-1",
        version_id="ver-1",
        source_file="a.pdf",
        observed_at=datetime(2024, 1, 1, tzinfo=UTC),
        status=DocumentVersionStatus.EFFECTIVE,
    )


def _versions() -> ApplicableVersionSet:
    return ApplicableVersionSet(
        as_of=datetime(2024, 6, 1, tzinfo=UTC), versions=[_version()], conflicts=[]
    )


def _decision(status: DecisionStatus) -> DecisionResult:
    return DecisionResult(status=status, reason_codes=["R1"], summary="test")


def _rule() -> RuleRef:
    return RuleRef(rule_id="rule-1", rule_version="1.0.0")


def _fact() -> ExplicitFact:
    return ExplicitFact(
        id="f-1",
        fact_type="amount",
        subject_id="s-1",
        value="100",
        evidence=[
            EvidenceSpan(
                id="span-1",
                source_file="a.pdf",
                document_id="doc-1",
                document_version_id="ver-1",
                page_number=1,
                quote="100 KZT",
            )
        ],
    )


def _calc() -> CalculatedValue:
    return CalculatedValue(
        id="c-1",
        value=Decimal("100"),
        currency="KZT",
        trace=CalculationTrace(
            operation="identity",
            formula="value",
            algorithm_version="1.0.0",
            included_record_ids=["f-1"],
            excluded_records={},
            parameters={},
        ),
    )


def test_proof_bundle_requires_an_applicable_version() -> None:
    empty_versions = ApplicableVersionSet(
        as_of=datetime(2024, 6, 1, tzinfo=UTC),
        versions=[],
        conflicts=[],
    )
    with pytest.raises(ValidationError, match="applicable version"):
        ProofBundle(
            case_id="case-1",
            decision=_decision(DecisionStatus.NEEDS_REVIEW),
            applicable_versions=empty_versions,
            explicit_facts=[_fact()],
            derived_facts=[],
            calculations=[_calc()],
            rules=[],
        )


def test_approve_requires_at_least_one_rule() -> None:
    with pytest.raises(ValidationError, match="APPROVE"):
        ProofBundle(
            case_id="case-1",
            decision=_decision(DecisionStatus.APPROVE),
            applicable_versions=_versions(),
            explicit_facts=[_fact()],
            derived_facts=[],
            calculations=[_calc()],
            rules=[],
        )


def test_reject_requires_at_least_one_rule() -> None:
    with pytest.raises(ValidationError, match="REJECT"):
        ProofBundle(
            case_id="case-1",
            decision=_decision(DecisionStatus.REJECT),
            applicable_versions=_versions(),
            explicit_facts=[_fact()],
            derived_facts=[],
            calculations=[_calc()],
            rules=[],
        )


def test_needs_review_does_not_require_rules() -> None:
    bundle = ProofBundle(
        case_id="case-1",
        decision=_decision(DecisionStatus.NEEDS_REVIEW),
        applicable_versions=_versions(),
        explicit_facts=[_fact()],
        derived_facts=[],
        calculations=[_calc()],
        rules=[],
    )
    assert bundle.decision.status is DecisionStatus.NEEDS_REVIEW
    assert bundle.rules == []


def test_insufficient_evidence_does_not_require_rules() -> None:
    bundle = ProofBundle(
        case_id="case-1",
        decision=_decision(DecisionStatus.INSUFFICIENT_EVIDENCE),
        applicable_versions=_versions(),
        explicit_facts=[],
        derived_facts=[],
        calculations=[],
        rules=[],
    )
    assert bundle.decision.status is DecisionStatus.INSUFFICIENT_EVIDENCE


def test_approve_with_rule_is_valid() -> None:
    bundle = ProofBundle(
        case_id="case-1",
        decision=_decision(DecisionStatus.APPROVE),
        applicable_versions=_versions(),
        explicit_facts=[_fact()],
        derived_facts=[],
        calculations=[_calc()],
        rules=[_rule()],
    )
    assert len(bundle.rules) == 1
