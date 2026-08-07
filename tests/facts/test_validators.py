"""Validator tests for evidence and semantic gates."""

from __future__ import annotations

from decimal import Decimal

from halyk_agent.domain.authority.models import AuthorityDomain
from halyk_agent.domain.fact_extraction.models import (
    DerivationKind,
    ExtractionMethod,
    FactCandidate,
    FactKind,
    FactRequirement,
    FactValidatorStatus,
    FxRatePayload,
    MoneyAmount,
    OffLedgerAmountPayload,
    OwnershipPayload,
    RateSource,
    TransactionReclassificationPayload,
)
from halyk_agent.domain.fact_extraction.validators import (
    semantic_validate,
    validate_candidate,
    validate_evidence,
)
from halyk_agent.domain.fact_extraction.windows import EvidenceFragment, EvidenceWindow
from tests.authority.helpers import make_document


def _req(kind: FactKind = FactKind.OWNERSHIP) -> FactRequirement:
    return FactRequirement(
        requirement_id="r1",
        scenario_id="S1",
        fact_kind=kind,
        derivation_kind=DerivationKind.SEMANTIC_REQUIRED,
        trigger_rule="t",
        allowed_authority_domains=(AuthorityDomain.KYC_RELATIONSHIPS,),
        reason_code="T",
    )


def _cand(
    *,
    quote: str,
    page: int,
    start: int,
    end: int,
    doc_id: str,
    payload: object,
    kind: FactKind = FactKind.OWNERSHIP,
    domain: AuthorityDomain = AuthorityDomain.KYC_RELATIONSHIPS,
    fragment_ids: tuple[str, ...] = (),
) -> FactCandidate:
    return FactCandidate(
        candidate_id="c1",
        requirement_id="r1",
        scenario_id="S1",
        fact_kind=kind,
        payload=payload,  # type: ignore[arg-type]
        authority_domain=domain,
        source_document_id=doc_id,
        source_file="doc.pdf",
        source_sha256="a" * 64,
        extraction_method=ExtractionMethod.DETERMINISTIC,
        reason_code="T",
        quote=quote,
        page_number=page,
        char_start=start,
        char_end=end,
        fragment_ids=fragment_ids,
    )


def test_invented_quote_rejected() -> None:
    text = "Ertis Capital, LLP 31.4%"
    doc = make_document(raw_text=text)
    cand = _cand(
        quote="this quote does not exist in the document at all",
        page=1,
        start=0,
        end=10,
        doc_id=doc.document_id,
        payload=OwnershipPayload(
            entity_name="Ertis Capital, LLP", ownership_percent=Decimal("31.4")
        ),
    )
    status, span, reason = validate_evidence(
        cand,
        doc,
        authoritative_doc_ids={doc.document_id},
    )
    assert status is FactValidatorStatus.REJECTED_EVIDENCE
    assert span is None
    assert reason == "QUOTE_NOT_FOUND"


def test_wrong_domain_doc_rejected() -> None:
    text = "Ertis Capital, LLP 31.4%"
    doc = make_document(raw_text=text)
    cand = _cand(
        quote=text,
        page=1,
        start=0,
        end=len(text),
        doc_id=doc.document_id,
        payload=OwnershipPayload(
            entity_name="Ertis Capital, LLP", ownership_percent=Decimal("31.4")
        ),
    )
    status, _, reason = validate_evidence(
        cand,
        doc,
        authoritative_doc_ids={"other-doc"},
    )
    assert status is FactValidatorStatus.REJECTED_EVIDENCE
    assert reason == "NON_AUTHORITATIVE_DOC"


def test_bad_ownership_percent() -> None:
    status, reason = semantic_validate(
        OwnershipPayload(entity_name="X Corp", ownership_percent=Decimal("50"))
    )
    assert status is FactValidatorStatus.ACCEPTED
    bad = OwnershipPayload.model_construct(
        kind=FactKind.OWNERSHIP,
        entity_name="X Corp",
        ownership_percent=Decimal("150"),
        holder_label="GROUP",
        voting_rights=True,
    )
    status, reason = semantic_validate(bad)
    assert status is FactValidatorStatus.REJECTED_SEMANTIC
    assert reason == "OWNERSHIP_OUT_OF_RANGE"


def test_ownership_legal_form_only_rejected() -> None:
    status, reason = semantic_validate(
        OwnershipPayload(entity_name="LLP", ownership_percent=Decimal("10"))
    )
    assert status is FactValidatorStatus.REJECTED_SEMANTIC
    assert reason == "OWNERSHIP_LEGAL_FORM_ONLY"


def test_bad_fx_and_unknown_txn() -> None:
    bad_fx = FxRatePayload.model_construct(
        kind=FactKind.FX_RATE,
        from_currency="USD",
        to_currency="EUR",
        source_amount=None,
        settlement_amount=None,
        explicit_rate=Decimal("0"),
        rate_source=RateSource.EXPLICIT,
        as_of_date=None,
        transaction_id=None,
    )
    status, reason = semantic_validate(bad_fx)
    assert status is FactValidatorStatus.REJECTED_SEMANTIC
    assert reason == "FX_NON_POSITIVE"

    payload = TransactionReclassificationPayload(
        transaction_id="TXN-S1-999",
        from_category="A",
        to_category="B",
        amount=MoneyAmount(value=Decimal("1"), currency="USD"),
    )
    status, reason = semantic_validate(payload, ledger_txn_ids={"TXN-S1-001"})
    assert status is FactValidatorStatus.REJECTED_SEMANTIC
    assert reason == "UNKNOWN_TXN"


def test_off_ledger_ok_without_txn() -> None:
    payload = OffLedgerAmountPayload(
        label="severance",
        amount=MoneyAmount(value=Decimal("10"), currency="USD"),
    )
    status, reason = semantic_validate(payload, ledger_txn_ids={"TXN-S1-001"})
    assert status is FactValidatorStatus.ACCEPTED
    assert reason == "SEMANTIC_OK"


def test_validate_candidate_accepts_exact_quote() -> None:
    text = "Ertis Capital, LLP 31.4%"
    doc = make_document(raw_text=text)
    req = _req()
    cand = _cand(
        quote=text,
        page=1,
        start=0,
        end=len(text),
        doc_id=doc.document_id,
        payload=OwnershipPayload(
            entity_name="Ertis Capital, LLP",
            ownership_percent=Decimal("31.4"),
        ),
    )
    status, span, _ = validate_candidate(
        cand,
        doc,
        authoritative_doc_ids={doc.document_id},
        requirement=req,
    )
    assert status is FactValidatorStatus.ACCEPTED
    assert span is not None
    assert span.quote == text


def test_quote_outside_fragment_rejected() -> None:
    text = "AAAA ownership prefix. Ertis Capital, LLP 31.4% trailing noise BBBB"
    doc = make_document(raw_text=text)
    frag_text = "Ertis Capital, LLP 31.4%"
    frag_start = text.find(frag_text)
    window = EvidenceWindow(
        window_id="w",
        requirement_id="r1",
        document_id=doc.document_id,
        source_sha256=doc.source_sha256,
        fragments=(
            EvidenceFragment(
                fragment_id="F001",
                page_number=1,
                char_start=frag_start,
                char_end=frag_start + len(frag_text),
                text=frag_text,
            ),
        ),
        window_hash="h",
    )
    # Quote exists in document but outside the fragment interval.
    outside = "AAAA ownership prefix"
    cand = _cand(
        quote=outside,
        page=1,
        start=0,
        end=len(outside),
        doc_id=doc.document_id,
        payload=OwnershipPayload(
            entity_name="Ertis Capital, LLP", ownership_percent=Decimal("31.4")
        ),
        fragment_ids=("F001",),
    )
    status, _, reason = validate_evidence(
        cand,
        doc,
        authoritative_doc_ids={doc.document_id},
        requirement=_req(),
        window=window,
    )
    assert status is FactValidatorStatus.REJECTED_EVIDENCE
    assert reason == "QUOTE_OUTSIDE_FRAGMENT"
