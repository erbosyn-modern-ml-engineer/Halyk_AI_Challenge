"""Security boundary tests for Stage 5E."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from halyk_agent.app.facts import FactServiceError, assert_no_gt_access
from halyk_agent.domain.authority.models import AuthorityDomain, AuthorityStatus
from halyk_agent.domain.fact_extraction.engine import run_fact_extraction
from halyk_agent.domain.fact_extraction.models import (
    ExtractionMethod,
    FactCandidate,
    FactKind,
    FactRequirement,
    FactValidatorStatus,
    OwnershipPayload,
)
from halyk_agent.domain.fact_extraction.validators import validate_evidence
from halyk_agent.domain.fact_extraction.windows import (
    EvidenceFragment,
    EvidenceWindow,
    select_windows,
)
from tests.authority.helpers import make_document
from tests.facts.helpers import make_decision, make_definition, reclass_modifier


def test_gt_path_rejected() -> None:
    with pytest.raises(FactServiceError) as exc:
        assert_no_gt_access(Path("dataset/ground_truth.json"))
    assert exc.value.code == "GT_FORBIDDEN"


def test_non_authoritative_doc_cannot_become_accepted() -> None:
    text = (
        "Сумма в размере $100.00, выплаченная контрагенту Acme LLP, "
        "учтенная как OPEX, переклассифицирована как CAPEX."
    )
    auth_doc = make_document(artifact="auth", source_file="auth.pdf", raw_text="no facts here")
    other = make_document(artifact="other", source_file="other.pdf", raw_text=text, sha="c" * 64)
    definitions = (make_definition(modifiers=(reclass_modifier(),)),)
    # Authoritative winner is auth_doc (no reclass text); other has the text but is not winning.
    decisions = (
        make_decision(
            domain=AuthorityDomain.FINANCIAL_ADJUSTMENTS,
            status=AuthorityStatus.AUTHORITATIVE,
            winning=(auth_doc.document_id,),
        ),
    )
    report = run_fact_extraction(
        definitions=definitions,
        decisions=decisions,
        documents=(auth_doc, other),
    )
    assert all(f.source_document_id == auth_doc.document_id for f in report.accepted_facts)
    assert other.document_id not in {f.source_document_id for f in report.accepted_facts}


def test_fragment_not_in_window_rejected() -> None:
    text = "Ertis Capital, LLP 31.4% ownership table"
    doc = make_document(raw_text=text)
    req = FactRequirement(
        requirement_id="r1",
        scenario_id="S1",
        fact_kind=FactKind.OWNERSHIP,
        authority_domain=AuthorityDomain.KYC_RELATIONSHIPS,
        reason_code="T",
        lexical_cues=("ownership", "%"),
    )
    window = select_windows(req, doc)
    assert window is not None
    # Craft a candidate claiming a fragment id outside the window
    fake_window = EvidenceWindow(
        window_id="w",
        requirement_id=req.requirement_id,
        document_id=doc.document_id,
        source_sha256=doc.source_sha256,
        fragments=(
            EvidenceFragment(
                fragment_id="F001",
                page_number=1,
                char_start=0,
                char_end=len(text),
                text=text,
            ),
        ),
        window_hash="h",
    )
    cand = FactCandidate(
        candidate_id="c",
        requirement_id=req.requirement_id,
        scenario_id="S1",
        fact_kind=FactKind.OWNERSHIP,
        payload=OwnershipPayload(
            entity_name="Ertis Capital, LLP",
            ownership_percent=Decimal("31.4"),
        ),
        authority_domain=AuthorityDomain.KYC_RELATIONSHIPS,
        source_document_id=doc.document_id,
        source_file=doc.source_file,
        source_sha256=doc.source_sha256,
        extraction_method=ExtractionMethod.LLM_PRIMARY,
        reason_code="T",
        quote=text,
        page_number=1,
        char_start=0,
        char_end=len(text),
        fragment_ids=("F999",),
    )
    status, _, reason = validate_evidence(
        cand,
        doc,
        authoritative_doc_ids={doc.document_id},
        requirement=req,
        window=fake_window,
    )
    assert status is FactValidatorStatus.REJECTED_EVIDENCE
    assert reason == "FRAGMENT_NOT_IN_WINDOW"
