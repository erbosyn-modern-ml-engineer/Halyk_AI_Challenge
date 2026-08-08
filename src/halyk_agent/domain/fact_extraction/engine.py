"""Stage 5E fact extraction engine (deterministic-first, gated model assist)."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import TypeAdapter, ValidationError

from halyk_agent.domain.authority.models import AuthorityDecision, AuthorityDomain, AuthorityStatus
from halyk_agent.domain.covenants.models import CovenantDefinition
from halyk_agent.domain.errors import EvidenceAlignmentError
from halyk_agent.domain.evidence import EvidenceSpan
from halyk_agent.domain.evidence_factory import create_exact_page_span
from halyk_agent.domain.fact_extraction.confirmed_none import detect_confirmed_none
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
from halyk_agent.domain.fact_extraction.extractors import (
    extract_candidates,
    has_incomplete_ppe_roll_forward,
)
from halyk_agent.domain.fact_extraction.models import (
    DerivationKind,
    ExtractionMethod,
    FactCandidate,
    FactExtractionManifest,
    FactExtractionReport,
    FactKind,
    FactPayload,
    FactRecord,
    FactRequirement,
    FactRequirementResult,
    FactValidatorStatus,
    ModelProvenance,
    RateSource,
    RequirementTerminalState,
)
from halyk_agent.domain.fact_extraction.requirements import derive_fact_requirements
from halyk_agent.domain.fact_extraction.text_normalize import cue_corpus
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


def hash_fact_models(models: Sequence[Any]) -> str:
    """Content hash matching Stage 5E manifest artifact hashes (file order)."""
    lines = [
        json.dumps(m.model_dump(mode="json"), ensure_ascii=False, sort_keys=True) for m in models
    ]
    return sha256_text("\n".join(lines))


def _hash_models(models: Sequence[Any]) -> str:
    return hash_fact_models(models)


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


def _doc_has_strong_cue(requirement: FactRequirement, document: CanonicalDocument) -> bool:
    cues = requirement.strong_lexical_cues or requirement.lexical_cues
    if not cues:
        return False
    for page in document.pages:
        text = cue_corpus(page.raw_text or "").casefold()
        if any(cue.casefold() in text for cue in cues if cue):
            return True
    return False


def _corpus_plausibly_answers(requirement: FactRequirement, corpus: str) -> bool:
    """Stricter than cue presence: look for parse-shaped evidence per family."""
    repaired = cue_corpus(corpus)
    lowered = repaired.casefold()
    kind = requirement.fact_kind
    if kind is FactKind.FX_RATE:
        return bool(
            re.search(
                r"(?:курс|exchange\s+rate)\s+(?:равен|составил|of|is)\s+\d",
                repaired,
                re.IGNORECASE,
            )
            or re.search(
                r"(?:счёт|invoice).{0,100}?(?:EUR|GBP).{0,140}?(?:урегулирован|settled)",
                repaired,
                re.IGNORECASE | re.DOTALL,
            )
            or re.search(
                r"(?:урегулирован|settled).{0,80}\$",
                repaired,
                re.IGNORECASE | re.DOTALL,
            )
        )
    if kind is FactKind.TRANSACTION_PERIOD:
        return bool(
            re.search(r"TXN-[A-Za-z0-9]+-\d+", repaired)
            and any(
                cue.casefold() in lowered
                for cue in (
                    "исключен",
                    "excluded",
                    "относится к услуг",
                    "оказанн",
                    "assign",
                    "cutoff",
                    "service period",
                )
            )
        )
    if kind is FactKind.AMOUNT_CORRECTION:
        return any(
            cue in lowered
            for cue in (
                "сумма не отражен",
                "фактическая сумма",
                "corrected amount",
                "actual amount",
                "уточн",
                "исправленн",
            )
        )
    if kind is FactKind.TRANSACTION_TREATMENT:
        return bool(re.search(r"TXN-[A-Za-z0-9]+-\d+", repaired)) and any(
            cue in lowered for cue in ("расчёт", "расчет", "covenant calculation", "из расч")
        )
    if kind is FactKind.OWNERSHIP:
        return bool(re.search(r"\d+(?:[.,]\d+)?\s*%", repaired)) and (
            any(cue in lowered for cue in ("владе", "ownership", "голосующ", "бенефициар"))
            or bool(re.search(r"(?:LLP|JSC|Inc)\s+\d+(?:[.,]\d+)?\s*%", repaired))
        )
    if kind is FactKind.RELATED_PARTY_THRESHOLD:
        return any(cue in lowered for cue in ("связанн", "related")) and "%" in repaired
    if kind is FactKind.SUBSIDIARY_STATUS:
        # Bare group/доли участия is insufficient.
        return any(
            cue in lowered
            for cue in (
                "subsidiary",
                "дочерн",
                "unrestricted",
                "restricted",
                "неограниченн",
                "ограниченн",
            )
        )
    if kind is FactKind.TRANSACTION_RECLASSIFICATION:
        return any(cue in lowered for cue in ("перекласс", "reclass", "переквалиф"))
    cues = requirement.strong_lexical_cues or requirement.lexical_cues
    return any(cue.casefold() in lowered for cue in cues if cue)


def _source_plausibly_contains_answer(
    requirement: FactRequirement,
    document: CanonicalDocument,
) -> bool:
    corpus = "\n".join((p.raw_text or "") for p in document.pages)
    return _corpus_plausibly_answers(requirement, corpus)


def _window_plausibly_answers(
    requirement: FactRequirement,
    window: object,
) -> bool:
    from halyk_agent.domain.fact_extraction.windows import EvidenceWindow

    if not isinstance(window, EvidenceWindow):
        return False
    corpus = "\n".join(f.text for f in window.fragments)
    return _corpus_plausibly_answers(requirement, corpus)


def _winning_domains_and_docs(
    requirement: FactRequirement,
    auth_map: dict[tuple[str, str], set[str]],
) -> list[tuple[AuthorityDomain, set[str]]]:
    out: list[tuple[AuthorityDomain, set[str]]] = []
    for domain in requirement.allowed_authority_domains:
        winning = auth_map.get((requirement.scenario_id, domain.value), set())
        if winning:
            out.append((domain, winning))
    return out


def _llm_candidate_from_result(
    *,
    requirement: FactRequirement,
    document: CanonicalDocument,
    authority_domain: AuthorityDomain,
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
        authority_domain=authority_domain,
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


def _evaluate_model_eligibility(
    *,
    requirement: FactRequirement,
    domain_docs: list[tuple[AuthorityDomain, set[str]]],
    docs: dict[str, CanonicalDocument],
    confirmed_none: bool,
    deterministic_resolved: bool,
) -> tuple[bool, str]:
    """Return (eligible, reason). NEEDS_MODEL only when all gates pass."""
    if confirmed_none:
        return False, "CONFIRMED_NONE"
    if deterministic_resolved:
        return False, "ALREADY_RESOLVED"
    if not domain_docs:
        return False, "NO_AUTHORITY"
    # Strong cue + window + plausibility on at least one winning doc
    saw_incomplete_ppe = False
    for domain, winning in domain_docs:
        for doc_id in sorted(winning):
            document = docs.get(doc_id)
            if document is None:
                continue
            if requirement.fact_kind is FactKind.GROUP_CAPEX and has_incomplete_ppe_roll_forward(
                document
            ):
                saw_incomplete_ppe = True
                continue
            if not _doc_has_strong_cue(requirement, document):
                continue
            if not _source_plausibly_contains_answer(requirement, document):
                continue
            window = select_windows(requirement, document)
            if window is None:
                continue
            if not _window_plausibly_answers(requirement, window):
                continue
            _ = domain
            return True, "MODEL_ELIGIBLE"
    if saw_incomplete_ppe and requirement.fact_kind is FactKind.GROUP_CAPEX:
        return False, "INCOMPLETE_PPE_ROLL_FORWARD"
    return False, "NO_STRONG_CUE_OR_WINDOW"


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
    Optional model gateway is fail-closed unless allow_network_models + gateway,
    and only for requirements that pass strict model-eligibility gates.
    """
    requirements = derive_fact_requirements(definitions, decisions, documents)
    auth_map = _authoritative_docs(decisions)
    docs = _docs_by_id(documents)
    txn_ids = _ledger_txn_ids(ledger_rows)

    candidates: list[FactCandidate] = []
    accepted: list[FactRecord] = []
    rejected: list[FactRecord] = []
    spans: dict[str, EvidenceSpan] = {}
    model_calls: list[ModelCallRecord] = []

    # Per-requirement bookkeeping
    resolved_req_ids: set[str] = set()
    confirmed_none_ids: set[str] = set()
    confirmed_none_spans: dict[str, str] = {}
    failed_validation_ids: set[str] = set()
    provider_unavailable_ids: set[str] = set()
    budget_exhausted_ids: set[str] = set()
    ambiguous_ids: set[str] = set()
    domains_used: dict[str, set[AuthorityDomain]] = {}
    accepted_by_req: dict[str, list[str]] = {}
    evidence_by_req: dict[str, list[str]] = {}
    model_eligible_flags: dict[str, bool] = {}
    eligibility_reasons: dict[str, str] = {}

    for requirement in requirements:
        domain_docs = _winning_domains_and_docs(requirement, auth_map)
        domains_used[requirement.requirement_id] = {d for d, _ in domain_docs}

        # Deterministic extraction first — collect ALL non-duplicate facts before
        # terminalization. CONFIRMED_NONE must not suppress specific REJECTED facts.
        for domain, winning in domain_docs:
            for doc_id in sorted(winning):
                document = docs.get(doc_id)
                if document is None:
                    continue
                for cand in extract_candidates(requirement, document, authority_domain=domain):
                    candidates.append(cand)
                    status, span, reason = validate_candidate(
                        cand,
                        document,
                        authoritative_doc_ids=winning,
                        requirement=requirement,
                        ledger_txn_ids=txn_ids,
                    )
                    record = _candidate_to_record(
                        cand, status=status, span=span, reason_code=reason
                    )
                    if status is FactValidatorStatus.ACCEPTED and span is not None:
                        spans[span.id] = span
                        accepted.append(record)
                        resolved_req_ids.add(requirement.requirement_id)
                        accepted_by_req.setdefault(requirement.requirement_id, []).append(
                            record.fact_id
                        )
                        evidence_by_req.setdefault(requirement.requirement_id, []).append(span.id)
                    else:
                        rejected.append(record)
                        if status in {
                            FactValidatorStatus.REJECTED_EVIDENCE,
                            FactValidatorStatus.REJECTED_SCHEMA,
                            FactValidatorStatus.REJECTED_SEMANTIC,
                        }:
                            failed_validation_ids.add(requirement.requirement_id)
                        if status is FactValidatorStatus.AMBIGUOUS:
                            ambiguous_ids.add(requirement.requirement_id)

        if requirement.requirement_id not in resolved_req_ids:
            none_hit = None
            none_doc = None
            none_domain = None
            for domain, winning in domain_docs:
                for doc_id in sorted(winning):
                    document = docs.get(doc_id)
                    if document is None:
                        continue
                    hit = detect_confirmed_none(requirement.fact_kind, document)
                    if hit is not None:
                        none_hit = hit
                        none_doc = document
                        none_domain = domain
                        break
                if none_hit is not None:
                    break
            if none_hit is not None and none_doc is not None and none_domain is not None:
                confirmed_none_ids.add(requirement.requirement_id)
                model_eligible_flags[requirement.requirement_id] = False
                eligibility_reasons[requirement.requirement_id] = "CONFIRMED_NONE"
                try:
                    none_span = create_exact_page_span(
                        none_doc, none_hit.page_number, none_hit.char_start, none_hit.char_end
                    )
                    spans[none_span.id] = none_span
                    confirmed_none_spans[requirement.requirement_id] = none_span.id
                    evidence_by_req.setdefault(requirement.requirement_id, []).append(none_span.id)
                except EvidenceAlignmentError:
                    pass
                domains_used[requirement.requirement_id].add(none_domain)
                continue

        eligible, elig_reason = _evaluate_model_eligibility(
            requirement=requirement,
            domain_docs=domain_docs,
            docs=docs,
            confirmed_none=requirement.requirement_id in confirmed_none_ids,
            deterministic_resolved=requirement.requirement_id in resolved_req_ids,
        )
        model_eligible_flags[requirement.requirement_id] = eligible
        eligibility_reasons[requirement.requirement_id] = elig_reason

    # Optional LLM path — only for model-eligible unresolved requirements
    if allow_network_models and model_gateway is not None:
        for requirement in requirements:
            if requirement.requirement_id in resolved_req_ids:
                continue
            if requirement.requirement_id in confirmed_none_ids:
                continue
            if not model_eligible_flags.get(requirement.requirement_id, False):
                continue

            domain_docs = _winning_domains_and_docs(requirement, auth_map)
            for domain, winning in domain_docs:
                if requirement.requirement_id in resolved_req_ids:
                    break
                for doc_id in sorted(winning):
                    if requirement.requirement_id in resolved_req_ids:
                        break
                    document = docs.get(doc_id)
                    if document is None:
                        continue
                    if not _doc_has_strong_cue(requirement, document):
                        continue
                    if not _source_plausibly_contains_answer(requirement, document):
                        continue
                    window = select_windows(requirement, document)
                    if window is None:
                        continue
                    if not _window_plausibly_answers(requirement, window):
                        continue

                    request = StructuredExtractionRequest(
                        requirement_id=requirement.requirement_id,
                        scenario_id=requirement.scenario_id,
                        fact_kind=requirement.fact_kind.value,
                        authority_domain=domain.value,
                        source_document_id=document.document_id,
                        source_sha256=document.source_sha256,
                        window_hash=window.window_hash,
                        fragments=tuple(f.model_dump(mode="json") for f in window.fragments),
                        lexical_cues=requirement.strong_lexical_cues or requirement.lexical_cues,
                    )
                    result, call = model_gateway.extract(request)
                    model_calls.append(call)

                    if result.state is ExtractionState.BUDGET_EXCEEDED:
                        budget_exhausted_ids.add(requirement.requirement_id)
                        continue
                    if result.state is ExtractionState.PROVIDER_ERROR:
                        provider_unavailable_ids.add(requirement.requirement_id)
                        continue
                    if result.state is ExtractionState.NETWORK_DISABLED:
                        provider_unavailable_ids.add(requirement.requirement_id)
                        continue
                    if result.state is not ExtractionState.RESOLVED or result.payload is None:
                        continue
                    if not result.evidence_fragment_ids:
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
                        authority_domain=domain,
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
                                authority_domain=domain,
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
                        failed_validation_ids.add(requirement.requirement_id)
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
                        accepted_by_req.setdefault(requirement.requirement_id, []).append(
                            record.fact_id
                        )
                        evidence_by_req.setdefault(requirement.requirement_id, []).append(span.id)
                    else:
                        rejected.append(record)
                        failed_validation_ids.add(requirement.requirement_id)
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
                                    authority_domain=domain,
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
                                        accepted_by_req.setdefault(
                                            requirement.requirement_id, []
                                        ).append(rec2.fact_id)
                                        evidence_by_req.setdefault(
                                            requirement.requirement_id, []
                                        ).append(sp2.id)
                                    else:
                                        rejected.append(rec2)

    if model_gateway is not None:
        for rec in model_gateway.call_records:
            if rec.call_id not in {c.call_id for c in model_calls}:
                model_calls.append(rec)

    accepted_t = dedupe_facts(tuple(accepted))
    conflicts = detect_conflicts(accepted_t)
    accepted_t = apply_conflicts(accepted_t, conflicts)
    final_accepted = tuple(
        f for f in accepted_t if f.validator_status is FactValidatorStatus.ACCEPTED
    )
    conflict_records = tuple(
        f for f in accepted_t if f.validator_status is FactValidatorStatus.CONFLICT
    )
    rejected_t = tuple(rejected) + conflict_records
    if conflicts:
        for conflict in conflicts:
            for fact_id in conflict.fact_ids:
                for fact in accepted_t:
                    if fact.fact_id == fact_id:
                        for rid in fact.requirement_ids:
                            ambiguous_ids.add(rid)

    # Rebuild accepted_by_req from final accepted
    accepted_by_req = {}
    evidence_by_req_final: dict[str, list[str]] = dict(evidence_by_req)
    for fact in final_accepted:
        for rid in fact.requirement_ids:
            accepted_by_req.setdefault(rid, []).append(fact.fact_id)
            resolved_req_ids.add(rid)
            evidence_by_req_final.setdefault(rid, []).extend(list(fact.evidence_span_ids))

    # Assign exactly one terminal state per requirement
    results: list[FactRequirementResult] = []
    for requirement in requirements:
        rid = requirement.requirement_id
        domain_docs = _winning_domains_and_docs(requirement, auth_map)
        if rid in confirmed_none_ids:
            state = RequirementTerminalState.CONFIRMED_NONE
            reason = "CONFIRMED_NONE"
            eligible = False
        elif rid in resolved_req_ids:
            state = RequirementTerminalState.RESOLVED
            reason = "RESOLVED"
            eligible = False
        elif rid in budget_exhausted_ids:
            state = RequirementTerminalState.BUDGET_EXHAUSTED
            reason = "BUDGET_EXHAUSTED"
            eligible = False
        elif rid in provider_unavailable_ids and allow_network_models:
            state = RequirementTerminalState.PROVIDER_UNAVAILABLE
            reason = "PROVIDER_UNAVAILABLE"
            eligible = False
        elif rid in ambiguous_ids:
            state = RequirementTerminalState.UNRESOLVED_AMBIGUOUS
            reason = "AMBIGUOUS"
            eligible = False
        elif not domain_docs:
            state = RequirementTerminalState.NOT_APPLICABLE
            reason = "NO_AUTHORITY_FOR_ALLOWED_DOMAINS"
            eligible = False
        elif model_eligible_flags.get(rid, False):
            # Eligible but unresolved (network off, or model returned nothing usable)
            if rid in failed_validation_ids and allow_network_models:
                state = RequirementTerminalState.FAILED_VALIDATION
                reason = "FAILED_VALIDATION"
            else:
                state = RequirementTerminalState.NEEDS_MODEL
                reason = eligibility_reasons.get(rid, "NEEDS_MODEL")
            eligible = True
        elif rid in failed_validation_ids and candidates:
            state = RequirementTerminalState.FAILED_VALIDATION
            reason = "FAILED_VALIDATION"
            eligible = False
        else:
            state = RequirementTerminalState.ABSENT_FROM_SOURCE
            reason = eligibility_reasons.get(rid, "ABSENT_FROM_SOURCE")
            eligible = False

        results.append(
            FactRequirementResult(
                requirement_id=rid,
                scenario_id=requirement.scenario_id,
                fact_kind=requirement.fact_kind,
                derivation_kind=requirement.derivation_kind,
                terminal_state=state,
                reason_code=reason,
                model_eligible=eligible
                and state
                in {
                    RequirementTerminalState.NEEDS_MODEL,
                    RequirementTerminalState.FAILED_VALIDATION,
                },
                accepted_fact_ids=tuple(accepted_by_req.get(rid, ())),
                evidence_span_ids=tuple(dict.fromkeys(evidence_by_req_final.get(rid, ()))),
                authority_domains_used=tuple(
                    sorted(domains_used.get(rid, set()), key=lambda d: d.value)
                ),
            )
        )

    unresolved = tuple(
        r.requirement_id
        for r in results
        if r.terminal_state
        not in {
            RequirementTerminalState.RESOLVED,
            RequirementTerminalState.CONFIRMED_NONE,
            RequirementTerminalState.NOT_APPLICABLE,
        }
    )

    docs_hash = sha256_text(
        "|".join(sorted(f"{d.document_id}:{d.source_sha256}" for d in documents))
    )
    req_hash = _hash_models(requirements)
    accepted_hash = _hash_models(final_accepted)
    evidence_hash = sha256_text("|".join(sorted(spans.keys())))
    results_hash = _hash_models(results)

    det_accepted = sum(
        1 for f in final_accepted if f.extraction_method is ExtractionMethod.DETERMINISTIC
    )
    llm_accepted = sum(
        1
        for f in final_accepted
        if f.extraction_method
        in {ExtractionMethod.LLM_PRIMARY, ExtractionMethod.LLM_ESCALATION, ExtractionMethod.MERGED}
    )

    terminal_counts: dict[str, int] = {}
    for r in results:
        terminal_counts[r.terminal_state.value] = terminal_counts.get(r.terminal_state.value, 0) + 1

    semantic_n = sum(
        1 for r in requirements if r.derivation_kind is DerivationKind.SEMANTIC_REQUIRED
    )
    source_n = sum(
        1 for r in requirements if r.derivation_kind is DerivationKind.SOURCE_TRIGGERED_CONDITIONAL
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
        semantic_required_count=semantic_n,
        source_triggered_count=source_n,
        speculative_count=0,
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
        terminal_state_counts=terminal_counts,
        needs_model_count=terminal_counts.get(RequirementTerminalState.NEEDS_MODEL.value, 0),
        confirmed_none_count=terminal_counts.get(RequirementTerminalState.CONFIRMED_NONE.value, 0),
        requirements_hash=req_hash,
        accepted_facts_hash=accepted_hash,
        evidence_hash=evidence_hash,
        requirement_results_hash=results_hash,
    )
    _ = FACT_ALGORITHM_VERSION

    return FactExtractionReport(
        manifest=manifest,
        requirements=requirements,
        requirement_results=tuple(results),
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
        return TransactionReclassificationPayload(
            from_category="A",
            to_category="B",
        )
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
        return OwnershipPayload(entity_name="X Corp", ownership_percent=Decimal("1"))
    if kind is FactKind.RELATED_PARTY_THRESHOLD:
        return RelatedPartyThresholdPayload(threshold_percent=Decimal("1"))
    if kind is FactKind.SUBSIDIARY_STATUS:
        return SubsidiaryStatusPayload(entity_name="X Corp", status=SubsidiaryKind.GROUP_MEMBER)
    if kind is FactKind.FX_RATE:
        return FxRatePayload(
            from_currency="USD",
            to_currency="EUR",
            explicit_rate=Decimal("1"),
            rate_source=RateSource.EXPLICIT,
        )
    if kind is FactKind.ONE_TIME_ADD_BACK:
        return OneTimeAddBackPayload(
            label="x", amount=MoneyAmount(value=Decimal("1"), currency="USD")
        )
    return TransactionTreatmentPayload(
        transaction_id="TXN-X-0", disposition=TreatmentDisposition.EXCLUDE
    )
