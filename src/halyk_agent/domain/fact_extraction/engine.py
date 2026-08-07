"""Stage 5E fact extraction engine (deterministic-first, optional model gateway)."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import TypeAdapter, ValidationError

from halyk_agent.domain.authority.models import AuthorityDecision, AuthorityStatus
from halyk_agent.domain.covenants.models import CovenantDefinition
from halyk_agent.domain.evidence import EvidenceSpan
from halyk_agent.domain.fact_extraction.conflicts import (
    apply_conflicts,
    content_fact_id,
    dedupe_facts,
    detect_conflicts,
)
from halyk_agent.domain.fact_extraction.constants import (
    FACT_ALGORITHM_VERSION,
    FACT_EXTRACTOR_VERSION,
    FACT_SCHEMA_VERSION,
    FACT_VALIDATOR_VERSION,
)
from halyk_agent.domain.fact_extraction.extractors import extract_candidates
from halyk_agent.domain.fact_extraction.models import (
    ExtractionMethod,
    FactCandidate,
    FactExtractionManifest,
    FactExtractionReport,
    FactKind,
    FactPayload,
    FactRecord,
    FactRequirement,
    FactValidatorStatus,
    ModelProvenance,
)
from halyk_agent.domain.fact_extraction.requirements import derive_fact_requirements
from halyk_agent.domain.fact_extraction.validators import validate_candidate
from halyk_agent.domain.fact_extraction.windows import select_windows
from halyk_agent.domain.ids import sha256_text
from halyk_agent.domain.models_gateway.gateway import StructuredModelGateway
from halyk_agent.domain.models_gateway.types import (
    ExtractionState,
    ModelCallRecord,
    StructuredExtractionRequest,
)
from halyk_agent.domain.parsing import CanonicalDocument
from halyk_agent.domain.routing.models import LedgerRow

_PAYLOAD_ADAPTER: TypeAdapter[FactPayload] = TypeAdapter(FactPayload)


def _authoritative_docs(
    decisions: tuple[AuthorityDecision, ...],
) -> dict[tuple[str, str], set[str]]:
    """Map (scenario_id, domain.value) → winning authoritative document ids."""
    out: dict[tuple[str, str], set[str]] = {}
    for decision in decisions:
        if decision.status is not AuthorityStatus.AUTHORITATIVE:
            continue
        key = (decision.scenario_id, decision.domain.value)
        out.setdefault(key, set()).update(decision.winning_document_ids)
    return out


def _docs_by_id(documents: Sequence[CanonicalDocument]) -> dict[str, CanonicalDocument]:
    return {doc.document_id: doc for doc in documents}


def _ledger_txn_ids(ledger_rows: Sequence[LedgerRow] | None) -> set[str] | None:
    if ledger_rows is None:
        return None
    return {row.txn_id for row in ledger_rows}


def _hash_models(models: Sequence[Any]) -> str:
    lines = [
        json.dumps(m.model_dump(mode="json"), ensure_ascii=False, sort_keys=True) for m in models
    ]
    return sha256_text("\n".join(lines))


def _candidate_to_record(
    candidate: FactCandidate,
    *,
    status: FactValidatorStatus,
    span: EvidenceSpan | None,
    reason_code: str,
) -> FactRecord:
    span_ids = (span.id,) if span is not None else ()
    record = FactRecord(
        fact_id="pending",
        scenario_id=candidate.scenario_id,
        fact_kind=candidate.fact_kind,
        payload=candidate.payload,
        authority_domain=candidate.authority_domain,
        source_document_id=candidate.source_document_id,
        source_file=candidate.source_file,
        source_sha256=candidate.source_sha256,
        evidence_span_ids=span_ids,
        extraction_method=candidate.extraction_method,
        validator_status=status,
        requirement_ids=(candidate.requirement_id,) if candidate.requirement_id else (),
        reason_code=reason_code,
        model_provenance=candidate.model_provenance,
    )
    return record.model_copy(update={"fact_id": content_fact_id(record)})


def _llm_candidate_from_result(
    *,
    requirement: FactRequirement,
    document: CanonicalDocument,
    result_payload: Mapping[str, Any],
    quote: str,
    page_number: int,
    char_start: int,
    char_end: int,
    fragment_ids: tuple[str, ...],
    method: ExtractionMethod,
    provenance: ModelProvenance | None,
) -> FactCandidate | None:
    data = dict(result_payload)
    data.setdefault("kind", requirement.fact_kind.value)
    try:
        payload = _PAYLOAD_ADAPTER.validate_python(data)
    except ValidationError:
        return None
    return FactCandidate(
        candidate_id=sha256_text(
            f"{requirement.requirement_id}:{document.document_id}:{quote}:{page_number}"
        ),
        requirement_id=requirement.requirement_id,
        scenario_id=requirement.scenario_id,
        fact_kind=requirement.fact_kind,
        payload=payload,
        authority_domain=requirement.authority_domain,
        source_document_id=document.document_id,
        source_file=document.source_file,
        source_sha256=document.source_sha256,
        extraction_method=method,
        reason_code="LLM_EXTRACT",
        quote=quote,
        page_number=page_number,
        char_start=char_start,
        char_end=char_end,
        fragment_ids=fragment_ids,
        model_provenance=provenance,
    )


def run_fact_extraction(
    *,
    definitions: tuple[CovenantDefinition, ...],
    decisions: tuple[AuthorityDecision, ...],
    documents: tuple[CanonicalDocument, ...],
    ledger_rows: tuple[LedgerRow, ...] | None = None,
    allow_network_models: bool = False,
    model_gateway: StructuredModelGateway | None = None,
    authority_manifest_hash: str = "",
    covenant_definitions_hash: str = "",
) -> FactExtractionReport:
    """
    Demand-driven fact extraction.

    Does not mutate ledger rows and does not compute covenant actuals.
    Deterministic extractors run first on authoritative winning documents only.
    Optional model gateway is fail-closed unless allow_network_models + gateway.
    """
    requirements = derive_fact_requirements(definitions, decisions)
    auth_map = _authoritative_docs(decisions)
    docs = _docs_by_id(documents)
    txn_ids = _ledger_txn_ids(ledger_rows)

    candidates: list[FactCandidate] = []
    accepted: list[FactRecord] = []
    rejected: list[FactRecord] = []
    spans: dict[str, EvidenceSpan] = {}
    model_calls: list[ModelCallRecord] = []
    resolved_req_ids: set[str] = set()

    for requirement in requirements:
        key = (requirement.scenario_id, requirement.authority_domain.value)
        winning = auth_map.get(key, set())
        if not winning:
            continue
        for doc_id in sorted(winning):
            document = docs.get(doc_id)
            if document is None:
                continue
            for cand in extract_candidates(requirement, document):
                candidates.append(cand)
                status, span, reason = validate_candidate(
                    cand,
                    document,
                    authoritative_doc_ids=winning,
                    requirement=requirement,
                    ledger_txn_ids=txn_ids,
                )
                record = _candidate_to_record(cand, status=status, span=span, reason_code=reason)
                if status is FactValidatorStatus.ACCEPTED and span is not None:
                    spans[span.id] = span
                    accepted.append(record)
                    resolved_req_ids.add(requirement.requirement_id)
                else:
                    rejected.append(record)

    # Optional LLM path for unresolved requirements
    if allow_network_models and model_gateway is not None:
        for requirement in requirements:
            if requirement.requirement_id in resolved_req_ids:
                continue
            key = (requirement.scenario_id, requirement.authority_domain.value)
            winning = auth_map.get(key, set())
            for doc_id in sorted(winning):
                if requirement.requirement_id in resolved_req_ids:
                    break
                document = docs.get(doc_id)
                if document is None:
                    continue
                window = select_windows(requirement, document)
                if window is None:
                    continue
                request = StructuredExtractionRequest(
                    requirement_id=requirement.requirement_id,
                    scenario_id=requirement.scenario_id,
                    fact_kind=requirement.fact_kind.value,
                    authority_domain=requirement.authority_domain.value,
                    source_document_id=document.document_id,
                    source_sha256=document.source_sha256,
                    window_hash=window.window_hash,
                    fragments=tuple(f.model_dump(mode="json") for f in window.fragments),
                    lexical_cues=requirement.lexical_cues,
                )
                result, call = model_gateway.extract(request)
                model_calls.append(call)

                if result.state is not ExtractionState.RESOLVED or result.payload is None:
                    continue
                if not result.evidence_fragment_ids:
                    # Evidence absent → do not escalate; leave unresolved.
                    continue
                if result.quote is None or result.page_number is None:
                    continue
                if result.char_start is None or result.char_end is None:
                    continue

                method = (
                    ExtractionMethod.LLM_ESCALATION
                    if call.escalated
                    else ExtractionMethod.LLM_PRIMARY
                )
                provenance = ModelProvenance(
                    provider=call.provider.value,
                    model=call.model,
                    prompt_version=call.prompt_version,
                    schema_version=call.schema_version,
                    request_hash=call.request_hash,
                    call_id=call.call_id,
                    attempt=call.attempt,
                )
                llm_cand = _llm_candidate_from_result(
                    requirement=requirement,
                    document=document,
                    result_payload=result.payload,
                    quote=result.quote,
                    page_number=result.page_number,
                    char_start=result.char_start,
                    char_end=result.char_end,
                    fragment_ids=tuple(result.evidence_fragment_ids),
                    method=method,
                    provenance=provenance,
                )
                if llm_cand is None:
                    rejected.append(
                        FactRecord(
                            fact_id=sha256_text(f"schema-fail:{requirement.requirement_id}"),
                            scenario_id=requirement.scenario_id,
                            fact_kind=requirement.fact_kind,
                            payload=_dummy_payload(requirement.fact_kind),
                            authority_domain=requirement.authority_domain,
                            source_document_id=document.document_id,
                            source_file=document.source_file,
                            source_sha256=document.source_sha256,
                            extraction_method=method,
                            validator_status=FactValidatorStatus.REJECTED_SCHEMA,
                            requirement_ids=(requirement.requirement_id,),
                            reason_code="LLM_SCHEMA_INVALID",
                            model_provenance=provenance,
                        )
                    )
                    # Escalate only when we had a resolved payload that failed validation.
                    esc_result, esc_call = model_gateway.extract(
                        request, escalate_on_validation_failure=True
                    )
                    model_calls.append(esc_call)
                    _ = esc_result
                    continue

                candidates.append(llm_cand)
                status, span, reason = validate_candidate(
                    llm_cand,
                    document,
                    authoritative_doc_ids=winning,
                    requirement=requirement,
                    ledger_txn_ids=txn_ids,
                    window=window,
                )
                record = _candidate_to_record(
                    llm_cand, status=status, span=span, reason_code=reason
                )
                if status is FactValidatorStatus.ACCEPTED and span is not None:
                    spans[span.id] = span
                    accepted.append(record)
                    resolved_req_ids.add(requirement.requirement_id)
                else:
                    rejected.append(record)
                    if status in {
                        FactValidatorStatus.REJECTED_SEMANTIC,
                        FactValidatorStatus.REJECTED_EVIDENCE,
                    }:
                        esc_result, esc_call = model_gateway.extract(
                            request, escalate_on_validation_failure=True
                        )
                        model_calls.append(esc_call)
                        if (
                            esc_result.state is ExtractionState.RESOLVED
                            and esc_result.payload is not None
                            and esc_result.evidence_fragment_ids
                            and esc_result.quote
                            and esc_result.page_number is not None
                            and esc_result.char_start is not None
                            and esc_result.char_end is not None
                        ):
                            esc_cand = _llm_candidate_from_result(
                                requirement=requirement,
                                document=document,
                                result_payload=esc_result.payload,
                                quote=esc_result.quote,
                                page_number=esc_result.page_number,
                                char_start=esc_result.char_start,
                                char_end=esc_result.char_end,
                                fragment_ids=tuple(esc_result.evidence_fragment_ids),
                                method=ExtractionMethod.LLM_ESCALATION,
                                provenance=ModelProvenance(
                                    provider=esc_call.provider.value,
                                    model=esc_call.model,
                                    prompt_version=esc_call.prompt_version,
                                    schema_version=esc_call.schema_version,
                                    request_hash=esc_call.request_hash,
                                    call_id=esc_call.call_id,
                                    attempt=esc_call.attempt,
                                ),
                            )
                            if esc_cand is not None:
                                candidates.append(esc_cand)
                                st2, sp2, rs2 = validate_candidate(
                                    esc_cand,
                                    document,
                                    authoritative_doc_ids=winning,
                                    requirement=requirement,
                                    ledger_txn_ids=txn_ids,
                                    window=window,
                                )
                                rec2 = _candidate_to_record(
                                    esc_cand, status=st2, span=sp2, reason_code=rs2
                                )
                                if st2 is FactValidatorStatus.ACCEPTED and sp2 is not None:
                                    spans[sp2.id] = sp2
                                    accepted.append(rec2)
                                    resolved_req_ids.add(requirement.requirement_id)
                                else:
                                    rejected.append(rec2)

    if model_gateway is not None:
        # Include any gateway records not already captured
        for rec in model_gateway.call_records:
            if rec.call_id not in {c.call_id for c in model_calls}:
                model_calls.append(rec)

    accepted_t = dedupe_facts(tuple(accepted))
    conflicts = detect_conflicts(accepted_t)
    accepted_t = apply_conflicts(accepted_t, conflicts)
    # Split conflicted out of accepted
    final_accepted = tuple(
        f for f in accepted_t if f.validator_status is FactValidatorStatus.ACCEPTED
    )
    conflict_records = tuple(
        f for f in accepted_t if f.validator_status is FactValidatorStatus.CONFLICT
    )
    rejected_t = tuple(rejected) + conflict_records

    unresolved = tuple(
        r.requirement_id for r in requirements if r.requirement_id not in resolved_req_ids
    )

    docs_hash = sha256_text(
        "|".join(sorted(f"{d.document_id}:{d.source_sha256}" for d in documents))
    )
    req_hash = _hash_models(requirements)
    accepted_hash = _hash_models(final_accepted)
    evidence_hash = sha256_text("|".join(sorted(spans.keys())))

    det_accepted = sum(
        1 for f in final_accepted if f.extraction_method is ExtractionMethod.DETERMINISTIC
    )
    llm_accepted = sum(
        1
        for f in final_accepted
        if f.extraction_method
        in {ExtractionMethod.LLM_PRIMARY, ExtractionMethod.LLM_ESCALATION, ExtractionMethod.MERGED}
    )

    manifest = FactExtractionManifest(
        schema_version=FACT_SCHEMA_VERSION,
        extractor_version=FACT_EXTRACTOR_VERSION,
        validator_version=FACT_VALIDATOR_VERSION,
        authority_manifest_hash=authority_manifest_hash or "unknown",
        covenant_definitions_hash=covenant_definitions_hash or _hash_models(definitions),
        canonical_documents_hash=docs_hash,
        scenario_count=len({d.scenario_id for d in definitions}),
        requirement_count=len(requirements),
        candidate_count=len(candidates),
        accepted_count=len(final_accepted),
        rejected_count=len(rejected_t),
        unresolved_count=len(unresolved),
        conflict_count=len(conflicts),
        model_call_count=len(model_calls),
        deterministic_accepted_count=det_accepted,
        llm_accepted_count=llm_accepted,
        evidence_span_count=len(spans),
        allow_network_models=allow_network_models,
        requirements_hash=req_hash,
        accepted_facts_hash=accepted_hash,
        evidence_hash=evidence_hash,
    )
    _ = FACT_ALGORITHM_VERSION  # identity marker for manifests/docs

    return FactExtractionReport(
        manifest=manifest,
        requirements=requirements,
        candidates=tuple(candidates),
        accepted_facts=final_accepted,
        rejected_facts=rejected_t,
        unresolved_requirement_ids=unresolved,
        conflicts=conflicts,
        spans=tuple(spans[k] for k in sorted(spans)),
        model_calls=tuple(model_calls),
    )


def _dummy_payload(kind: FactKind) -> FactPayload:
    """Minimal valid payload used only for schema-rejection bookkeeping."""
    from decimal import Decimal

    from halyk_agent.domain.fact_extraction.models import (
        AmountCorrectionPayload,
        FxRatePayload,
        MoneyAmount,
        OffLedgerAmountPayload,
        OneTimeAddBackPayload,
        OwnershipPayload,
        PeriodDisposition,
        RelatedPartyThresholdPayload,
        SubsidiaryKind,
        SubsidiaryStatusPayload,
        TransactionPeriodPayload,
        TransactionReclassificationPayload,
        TransactionTreatmentPayload,
        TreatmentDisposition,
    )

    if kind is FactKind.TRANSACTION_RECLASSIFICATION:
        return TransactionReclassificationPayload(from_category="A", to_category="B")
    if kind is FactKind.TRANSACTION_PERIOD:
        return TransactionPeriodPayload(
            transaction_id="TXN-X-0",
            disposition=PeriodDisposition.EXCLUDE_FROM_PERIOD,
        )
    if kind is FactKind.AMOUNT_CORRECTION:
        return AmountCorrectionPayload(amount=MoneyAmount(value=Decimal("1"), currency="USD"))
    if kind is FactKind.OFF_LEDGER_AMOUNT:
        return OffLedgerAmountPayload(
            label="x", amount=MoneyAmount(value=Decimal("1"), currency="USD")
        )
    if kind is FactKind.OWNERSHIP:
        return OwnershipPayload(entity_name="X", ownership_percent=Decimal("1"))
    if kind is FactKind.RELATED_PARTY_THRESHOLD:
        return RelatedPartyThresholdPayload(threshold_percent=Decimal("1"))
    if kind is FactKind.SUBSIDIARY_STATUS:
        return SubsidiaryStatusPayload(entity_name="X", status=SubsidiaryKind.GROUP_MEMBER)
    if kind is FactKind.FX_RATE:
        return FxRatePayload(from_currency="USD", to_currency="EUR", rate=Decimal("1"))
    if kind is FactKind.ONE_TIME_ADD_BACK:
        return OneTimeAddBackPayload(
            label="x", amount=MoneyAmount(value=Decimal("1"), currency="USD")
        )
    return TransactionTreatmentPayload(
        transaction_id="TXN-X-0", disposition=TreatmentDisposition.EXCLUDE
    )
