"""Evidence and semantic validators for Stage 5E fact candidates."""

from __future__ import annotations

from decimal import Decimal

from pydantic import ValidationError

from halyk_agent.domain.errors import EvidenceAlignmentError
from halyk_agent.domain.evidence import EvidenceSpan
from halyk_agent.domain.evidence_factory import create_exact_page_span
from halyk_agent.domain.fact_extraction.models import (
    AmountCorrectionPayload,
    FactCandidate,
    FactPayload,
    FactRequirement,
    FactValidatorStatus,
    FxRatePayload,
    OffLedgerAmountPayload,
    OneTimeAddBackPayload,
    OwnershipPayload,
    RelatedPartyThresholdPayload,
    TransactionPeriodPayload,
    TransactionReclassificationPayload,
    TransactionTreatmentPayload,
)
from halyk_agent.domain.fact_extraction.text_locate import find_quote_offsets
from halyk_agent.domain.fact_extraction.windows import EvidenceWindow, fragment_ids_in_window
from halyk_agent.domain.parsing import CanonicalDocument


def validate_evidence(
    candidate: FactCandidate,
    document: CanonicalDocument,
    *,
    authoritative_doc_ids: set[str],
    requirement: FactRequirement | None = None,
    window: EvidenceWindow | None = None,
) -> tuple[FactValidatorStatus, EvidenceSpan | None, str]:
    """
    Validate quote alignment and authority domain source.

    Returns (status, span_or_none, reason_code).
    """
    if candidate.source_document_id not in authoritative_doc_ids:
        return FactValidatorStatus.REJECTED_EVIDENCE, None, "NON_AUTHORITATIVE_DOC"

    if requirement is not None and candidate.authority_domain is not requirement.authority_domain:
        return FactValidatorStatus.REJECTED_EVIDENCE, None, "WRONG_DOMAIN"

    if requirement is not None and candidate.fact_kind is not requirement.fact_kind:
        return FactValidatorStatus.REJECTED_SCHEMA, None, "WRONG_FACT_KIND"

    if window is not None:
        allowed = fragment_ids_in_window(window)
        if candidate.fragment_ids and not set(candidate.fragment_ids).issubset(allowed):
            return FactValidatorStatus.REJECTED_EVIDENCE, None, "FRAGMENT_NOT_IN_WINDOW"

    quote = candidate.quote
    page = next((p for p in document.pages if p.page_number == candidate.page_number), None)
    if page is None:
        return FactValidatorStatus.REJECTED_EVIDENCE, None, "PAGE_MISSING"

    page_text = page.raw_text or ""
    # Prefer declared offsets when they match the quote exactly.
    if (
        0 <= candidate.char_start < candidate.char_end <= len(page_text)
        and page_text[candidate.char_start : candidate.char_end] == quote
    ):
        start, end = candidate.char_start, candidate.char_end
        page_number = candidate.page_number
    else:
        located = find_quote_offsets(document, quote)
        if located is None:
            return FactValidatorStatus.REJECTED_EVIDENCE, None, "QUOTE_NOT_FOUND"
        page_number, start, end = located

    try:
        span = create_exact_page_span(document, page_number, start, end)
    except EvidenceAlignmentError:
        return FactValidatorStatus.REJECTED_EVIDENCE, None, "SPAN_ALIGNMENT_FAILED"

    return FactValidatorStatus.ACCEPTED, span, "EVIDENCE_OK"


def semantic_validate(
    payload: FactPayload,
    ledger_txn_ids: set[str] | None = None,
) -> tuple[FactValidatorStatus, str]:
    """Semantic checks independent of document evidence."""
    try:
        if isinstance(payload, OwnershipPayload):
            if payload.ownership_percent < 0 or payload.ownership_percent > Decimal("100"):
                return FactValidatorStatus.REJECTED_SEMANTIC, "OWNERSHIP_OUT_OF_RANGE"
        elif isinstance(payload, RelatedPartyThresholdPayload):
            if payload.threshold_percent < 0 or payload.threshold_percent > Decimal("100"):
                return FactValidatorStatus.REJECTED_SEMANTIC, "THRESHOLD_OUT_OF_RANGE"
        elif isinstance(payload, FxRatePayload):
            if payload.rate <= 0:
                return FactValidatorStatus.REJECTED_SEMANTIC, "FX_NON_POSITIVE"
            if not payload.from_currency or not payload.to_currency:
                return FactValidatorStatus.REJECTED_SEMANTIC, "FX_CURRENCY_EMPTY"
            if (
                payload.transaction_id is not None
                and ledger_txn_ids is not None
                and payload.transaction_id not in ledger_txn_ids
            ):
                return FactValidatorStatus.REJECTED_SEMANTIC, "UNKNOWN_TXN"
        elif isinstance(payload, TransactionReclassificationPayload):
            if payload.from_category.strip().casefold() == payload.to_category.strip().casefold():
                return FactValidatorStatus.REJECTED_SEMANTIC, "RECLASS_SAME_CATEGORY"
            if payload.amount is not None and not payload.amount.currency.strip():
                return FactValidatorStatus.REJECTED_SEMANTIC, "CURRENCY_EMPTY"
            if (
                payload.transaction_id is not None
                and ledger_txn_ids is not None
                and payload.transaction_id not in ledger_txn_ids
            ):
                return FactValidatorStatus.REJECTED_SEMANTIC, "UNKNOWN_TXN"
        elif isinstance(payload, TransactionPeriodPayload | TransactionTreatmentPayload):
            if ledger_txn_ids is not None and payload.transaction_id not in ledger_txn_ids:
                return FactValidatorStatus.REJECTED_SEMANTIC, "UNKNOWN_TXN"
        elif isinstance(payload, AmountCorrectionPayload):
            if not payload.amount.currency.strip():
                return FactValidatorStatus.REJECTED_SEMANTIC, "CURRENCY_EMPTY"
            if (
                payload.transaction_id is not None
                and ledger_txn_ids is not None
                and payload.transaction_id not in ledger_txn_ids
            ):
                return FactValidatorStatus.REJECTED_SEMANTIC, "UNKNOWN_TXN"
        elif isinstance(payload, OffLedgerAmountPayload | OneTimeAddBackPayload) and (
            not payload.amount.currency.strip()
        ):
            return FactValidatorStatus.REJECTED_SEMANTIC, "CURRENCY_EMPTY"
        # Ownership / RelatedPartyThreshold / SubsidiaryStatus: bounds enforced by models.
    except ValidationError:
        return FactValidatorStatus.REJECTED_SCHEMA, "PAYLOAD_VALIDATION_ERROR"

    return FactValidatorStatus.ACCEPTED, "SEMANTIC_OK"


def validate_candidate(
    candidate: FactCandidate,
    document: CanonicalDocument,
    *,
    authoritative_doc_ids: set[str],
    requirement: FactRequirement | None = None,
    ledger_txn_ids: set[str] | None = None,
    window: EvidenceWindow | None = None,
) -> tuple[FactValidatorStatus, EvidenceSpan | None, str]:
    """Run evidence then semantic validation."""
    status, span, reason = validate_evidence(
        candidate,
        document,
        authoritative_doc_ids=authoritative_doc_ids,
        requirement=requirement,
        window=window,
    )
    if status is not FactValidatorStatus.ACCEPTED:
        return status, None, reason
    sem_status, sem_reason = semantic_validate(candidate.payload, ledger_txn_ids)
    if sem_status is not FactValidatorStatus.ACCEPTED:
        return sem_status, span, sem_reason
    return FactValidatorStatus.ACCEPTED, span, reason
