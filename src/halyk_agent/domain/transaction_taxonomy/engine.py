"""Stage 5F orchestration: classify → adjust → enrich → calculation inputs."""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from halyk_agent.domain.covenants.ast import MetricCategory
from halyk_agent.domain.covenants.models import CovenantDefinition, ScopeKind
from halyk_agent.domain.fact_extraction.models import (
    AmountCorrectionPayload,
    FactKind,
    FactRecord,
    FxRatePayload,
    OffLedgerAmountPayload,
    OneTimeAddBackPayload,
    PeriodDisposition,
    ReclassificationDisposition,
    SubsidiaryKind,
    SubsidiaryStatusPayload,
    TransactionPeriodPayload,
    TransactionReclassificationPayload,
)
from halyk_agent.domain.ids import deterministic_id, sha256_text
from halyk_agent.domain.routing.models import LedgerRow, TransactionEntityLink
from halyk_agent.domain.routing.normalize import normalize_legal_name_keys
from halyk_agent.domain.transaction_taxonomy.category_labels import map_category_label
from halyk_agent.domain.transaction_taxonomy.classify import classify_description
from halyk_agent.domain.transaction_taxonomy.constants import (
    TAXONOMY_ALGORITHM_VERSION,
    TAXONOMY_CLASSIFIER_VERSION,
    TAXONOMY_SCHEMA_VERSION,
)
from halyk_agent.domain.transaction_taxonomy.models import (
    AdjustmentEvent,
    AdjustmentEventType,
    CalculationInput,
    ClassificationMethod,
    ClassificationStatus,
    ConflictKind,
    DerivedCalculationInput,
    EntityScopeKind,
    FactConsumptionEntry,
    InputSourceKind,
    PeriodMembershipHint,
    RelatedPartyBasis,
    RelatedPartyStatus,
    TaxonomyConflict,
    TaxonomyManifest,
    TaxonomyReport,
    UnresolvedReason,
    UnresolvedTransaction,
)
from halyk_agent.domain.transaction_taxonomy.related_party import (
    qualifying_related_parties,
    resolve_related_party,
)
from halyk_agent.domain.transaction_taxonomy.selectors import build_selector_coverage


def _parse_date(raw: str) -> date | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def _parse_amount(raw: str) -> Decimal | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _hash_models(models: list[Any]) -> str:
    payload = [m.model_dump(mode="json") if hasattr(m, "model_dump") else m for m in models]
    return sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))


def _abs_amount(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return abs(value)


def _link_index(
    links: tuple[TransactionEntityLink, ...],
) -> dict[str, TransactionEntityLink]:
    return {link.txn_id: link for link in links}


def _facts_by_kind(facts: tuple[FactRecord, ...], kind: FactKind) -> tuple[FactRecord, ...]:
    return tuple(f for f in facts if f.fact_kind is kind)


def _match_reclass_targets(
    fact: FactRecord,
    rows_by_txn: dict[str, LedgerRow],
    scenario_txns: set[str],
) -> list[str]:
    payload = fact.payload
    assert isinstance(payload, TransactionReclassificationPayload)
    if payload.transaction_id:
        if payload.transaction_id in rows_by_txn:
            return [payload.transaction_id]
        return []
    amount = payload.amount.value if payload.amount is not None else None
    if amount is None:
        return []
    hits = [
        tid
        for tid in scenario_txns
        if _abs_amount(_parse_amount(rows_by_txn[tid].amount)) == amount
    ]
    return sorted(hits)


def run_transaction_taxonomy(
    *,
    ledger_rows: tuple[LedgerRow, ...],
    transaction_links: tuple[TransactionEntityLink, ...],
    definitions: tuple[CovenantDefinition, ...],
    accepted_facts: tuple[FactRecord, ...],
    ledger_source_sha256: str,
    routing_manifest_hash: str,
    covenant_manifest_hash: str,
    facts_manifest_hash: str,
) -> TaxonomyReport:
    """
    Deterministic Stage 5F pipeline.

    Precedence:
      RAW_LEDGER → BASE_CLASSIFICATION → AUTHORITATIVE_RECLASS / REJECTED
      → AMOUNT_CORRECTION → PERIOD → RELATED_PARTY / SCOPE → CALCULATION_INPUT
    """
    links = _link_index(transaction_links)
    rows_by_txn = {row.txn_id: row for row in ledger_rows}
    scenario_txns = {tid for tid, link in links.items() if link.scenario_id is not None}

    qualifying = qualifying_related_parties(accepted_facts)
    threshold_scenarios = {
        f.scenario_id for f in accepted_facts if f.fact_kind is FactKind.RELATED_PARTY_THRESHOLD
    }
    ownership_scenarios = {
        f.scenario_id for f in accepted_facts if f.fact_kind is FactKind.OWNERSHIP
    }
    subsidiary_by_scenario: dict[str, list[FactRecord]] = {}
    for fact in _facts_by_kind(accepted_facts, FactKind.SUBSIDIARY_STATUS):
        subsidiary_by_scenario.setdefault(fact.scenario_id, []).append(fact)

    # --- Base classification for scenario-linked rows ---
    classified_mutable: dict[str, dict[str, Any]] = {}
    conflicts: list[TaxonomyConflict] = []
    unresolved: list[UnresolvedTransaction] = []

    for row in sorted(ledger_rows, key=lambda r: (r.row_index, r.txn_id)):
        link = links.get(row.txn_id)
        scenario_id = link.scenario_id if link is not None else None
        account_id = (
            (link.account_id_normalized or link.account_id_raw)
            if link is not None
            else row.account_id
        )
        original_amount = _parse_amount(row.amount)
        original_date = _parse_date(row.date)
        amount_reasons: list[UnresolvedReason] = []
        if original_amount is None and (row.amount or "").strip() == "":
            amount_reasons.append(UnresolvedReason.AMOUNT_MISSING)
        elif original_amount is None and (row.amount or "").strip():
            amount_reasons.append(UnresolvedReason.AMOUNT_INVALID)

        if scenario_id is None:
            classified_mutable[row.txn_id] = {
                "transaction_id": row.txn_id,
                "source_ledger": row.ledger_source_file,
                "source_row_index": row.row_index,
                "source_sha256": ledger_source_sha256,
                "scenario_id": None,
                "account_id": account_id,
                "original_amount": original_amount,
                "original_currency": row.currency,
                "effective_amount": original_amount,
                "effective_currency": row.currency,
                "original_category": None,
                "effective_category": None,
                "original_date": original_date,
                "effective_period_start": original_date,
                "effective_period_end": original_date,
                "period_membership": PeriodMembershipHint.IN_LEDGER_DATE,
                "period_excluded": False,
                "counterparty_raw": row.counterparty,
                "counterparty_identity_key": normalize_legal_name_keys(
                    row.counterparty
                ).identity_key,
                "description": row.description,
                "related_party_status": RelatedPartyStatus.UNKNOWN,
                "related_party_basis": RelatedPartyBasis.ROUTING_NOISE,
                "entity_scope": EntityScopeKind.UNKNOWN,
                "classification_status": ClassificationStatus.ROUTING_NOISE,
                "classification_method": ClassificationMethod.ROUTING_NOISE,
                "classification_rule": None,
                "applied_fact_ids": [],
                "rejected_fact_ids": [],
                "evidence_refs": [f"ledger:{row.row_index}:{row.txn_id}"],
                "unresolved_reasons": [UnresolvedReason.ROUTING_UNRESOLVED],
                "conflict_ids": [],
                "flags": [],
            }
            continue

        hit = classify_description(row.description)
        status: ClassificationStatus
        method: ClassificationMethod
        reasons: list[UnresolvedReason] = list(amount_reasons)
        conflict_ids: list[str] = []
        if hit.status == "CLASSIFIED":
            status = ClassificationStatus.CLASSIFIED
            method = ClassificationMethod.LEDGER_DESCRIPTION_RULE
        elif hit.status == "CONFLICT":
            status = ClassificationStatus.CONFLICT
            method = ClassificationMethod.CONFLICT
            reasons.append(UnresolvedReason.CATEGORY_CONFLICT)
            cid = deterministic_id(
                "txn-conflict",
                ConflictKind.CATEGORY_COLLISION.value,
                row.txn_id,
                ",".join(hit.competing),
            )
            conflict_ids.append(cid)
            conflicts.append(
                TaxonomyConflict(
                    conflict_id=cid,
                    kind=ConflictKind.CATEGORY_COLLISION,
                    scenario_id=scenario_id,
                    transaction_id=row.txn_id,
                    reason="multiple strong category rules matched",
                    details={"competing_rules": list(hit.competing)},
                )
            )
        else:
            status = ClassificationStatus.UNRESOLVED
            method = ClassificationMethod.UNRESOLVED
            reasons.append(UnresolvedReason.CATEGORY_UNKNOWN)

        classified_mutable[row.txn_id] = {
            "transaction_id": row.txn_id,
            "source_ledger": row.ledger_source_file,
            "source_row_index": row.row_index,
            "source_sha256": ledger_source_sha256,
            "scenario_id": scenario_id,
            "account_id": account_id,
            "original_amount": original_amount,
            "original_currency": row.currency,
            "effective_amount": original_amount,
            "effective_currency": row.currency,
            "original_category": hit.category,
            "effective_category": hit.category,
            "original_date": original_date,
            "effective_period_start": original_date,
            "effective_period_end": original_date,
            "period_membership": PeriodMembershipHint.IN_LEDGER_DATE,
            "period_excluded": False,
            "counterparty_raw": row.counterparty,
            "counterparty_identity_key": normalize_legal_name_keys(row.counterparty).identity_key,
            "description": row.description,
            "related_party_status": RelatedPartyStatus.UNKNOWN,
            "related_party_basis": RelatedPartyBasis.MISSING_OWNERSHIP,
            "entity_scope": EntityScopeKind.BORROWER,
            "classification_status": status,
            "classification_method": method,
            "classification_rule": hit.rule,
            "applied_fact_ids": [],
            "rejected_fact_ids": [],
            "evidence_refs": [f"ledger:{row.row_index}:{row.txn_id}"],
            "unresolved_reasons": reasons,
            "conflict_ids": conflict_ids,
            "flags": [],
        }

    adjustments: list[AdjustmentEvent] = []
    fact_consumption: dict[str, FactConsumptionEntry] = {}
    derived_inputs: list[DerivedCalculationInput] = []

    def _mark_fact(
        fact: FactRecord,
        disposition: str,
        explanation: str,
    ) -> None:
        fact_consumption[fact.fact_id] = FactConsumptionEntry(
            fact_id=fact.fact_id,
            fact_kind=fact.fact_kind.value,
            scenario_id=fact.scenario_id,
            disposition=disposition,  # type: ignore[arg-type]
            explanation=explanation,
        )

    # --- Reclassifications ---
    reclass_facts = _facts_by_kind(accepted_facts, FactKind.TRANSACTION_RECLASSIFICATION)
    accepted_by_txn: dict[str, list[FactRecord]] = {}
    for fact in reclass_facts:
        payload = fact.payload
        assert isinstance(payload, TransactionReclassificationPayload)
        scenario_set = {tid for tid in scenario_txns if tid.split("-")[1] == fact.scenario_id} | {
            tid for tid, link in links.items() if link.scenario_id == fact.scenario_id
        }
        targets = _match_reclass_targets(fact, rows_by_txn, scenario_set)
        if payload.disposition is ReclassificationDisposition.REJECTED:
            if not targets and payload.transaction_id:
                _mark_fact(fact, "UNUSED", "rejected reclass target txn missing from ledger")
                continue
            for tid in targets:
                state = classified_mutable.get(tid)
                if state is None:
                    continue
                state["rejected_fact_ids"].append(fact.fact_id)
                state["evidence_refs"].extend(list(fact.evidence_span_ids))
                event_id = deterministic_id(
                    "adj",
                    AdjustmentEventType.CATEGORY_RECLASSIFICATION_REJECTED.value,
                    fact.fact_id,
                    tid,
                )
                adjustments.append(
                    AdjustmentEvent(
                        event_id=event_id,
                        event_type=AdjustmentEventType.CATEGORY_RECLASSIFICATION_REJECTED,
                        scenario_id=fact.scenario_id,
                        fact_id=fact.fact_id,
                        transaction_id=tid,
                        before={
                            "effective_category": (
                                state["effective_category"].value
                                if state["effective_category"]
                                else None
                            )
                        },
                        after={
                            "effective_category": (
                                state["effective_category"].value
                                if state["effective_category"]
                                else None
                            ),
                            "proposal_preserved": True,
                            "proposed_to": payload.to_category,
                        },
                        evidence_span_ids=fact.evidence_span_ids,
                        authority_domain=fact.authority_domain.value,
                        reason_code="REJECTED_RECLASS_PRESERVED",
                    )
                )
            _mark_fact(fact, "CONSUMED", "rejected reclassification preserved; category unchanged")
            continue

        # ACCEPTED
        if len(targets) == 0:
            _mark_fact(fact, "UNUSED", "accepted reclass could not be linked to a ledger row")
            continue
        if len(targets) > 1:
            cid = deterministic_id(
                "txn-conflict",
                ConflictKind.FACT_LINK_AMBIGUOUS.value,
                fact.fact_id,
            )
            conflicts.append(
                TaxonomyConflict(
                    conflict_id=cid,
                    kind=ConflictKind.FACT_LINK_AMBIGUOUS,
                    scenario_id=fact.scenario_id,
                    fact_ids=(fact.fact_id,),
                    reason="accepted reclass amount matched multiple ledger rows",
                    details={"targets": targets},
                )
            )
            _mark_fact(fact, "UNUSED", "ambiguous ledger link for accepted reclass")
            continue
        accepted_by_txn.setdefault(targets[0], []).append(fact)

    for tid, facts in sorted(accepted_by_txn.items()):
        state = classified_mutable[tid]
        mapped: list[tuple[FactRecord, MetricCategory]] = []
        for fact in facts:
            payload = fact.payload
            assert isinstance(payload, TransactionReclassificationPayload)
            cat = map_category_label(payload.to_category)
            if cat is None:
                _mark_fact(fact, "UNUSED", f"unknown to_category label: {payload.to_category}")
                continue
            mapped.append((fact, cat))
        categories = {c for _, c in mapped}
        if len(categories) > 1:
            cid = deterministic_id(
                "txn-conflict",
                ConflictKind.ACCEPTED_RECLASS_CONFLICT.value,
                tid,
            )
            conflicts.append(
                TaxonomyConflict(
                    conflict_id=cid,
                    kind=ConflictKind.ACCEPTED_RECLASS_CONFLICT,
                    scenario_id=state["scenario_id"],
                    transaction_id=tid,
                    fact_ids=tuple(f.fact_id for f, _ in mapped),
                    reason="conflicting ACCEPTED reclassifications",
                )
            )
            state["classification_status"] = ClassificationStatus.CONFLICT
            state["conflict_ids"].append(cid)
            state["unresolved_reasons"].append(UnresolvedReason.FACT_CONFLICT)
            for fact, _ in mapped:
                _mark_fact(fact, "UNUSED", "conflicting accepted reclassifications")
            continue
        if not mapped:
            continue
        # Apply all ACCEPTED facts that agree on destination (no last-write-wins drop).
        dest = next(iter(categories))
        before = state["effective_category"].value if state["effective_category"] else None
        state["effective_category"] = dest
        state["classification_status"] = ClassificationStatus.CLASSIFIED
        state["classification_method"] = ClassificationMethod.AUTHORITATIVE_RECLASSIFICATION
        # Remove CATEGORY_UNKNOWN if present
        state["unresolved_reasons"] = [
            r
            for r in state["unresolved_reasons"]
            if r
            not in {
                UnresolvedReason.CATEGORY_UNKNOWN,
                UnresolvedReason.CATEGORY_CONFLICT,
            }
        ]
        for fact, _ in mapped:
            payload = fact.payload
            assert isinstance(payload, TransactionReclassificationPayload)
            state["applied_fact_ids"].append(fact.fact_id)
            state["evidence_refs"].extend(list(fact.evidence_span_ids))
            adjustments.append(
                AdjustmentEvent(
                    event_id=deterministic_id(
                        "adj",
                        AdjustmentEventType.CATEGORY_RECLASSIFICATION_ACCEPTED.value,
                        fact.fact_id,
                        tid,
                    ),
                    event_type=AdjustmentEventType.CATEGORY_RECLASSIFICATION_ACCEPTED,
                    scenario_id=fact.scenario_id,
                    fact_id=fact.fact_id,
                    transaction_id=tid,
                    before={"effective_category": before},
                    after={
                        "effective_category": dest.value,
                        "from_category": payload.from_category,
                        "to_category": payload.to_category,
                    },
                    evidence_span_ids=fact.evidence_span_ids,
                    authority_domain=fact.authority_domain.value,
                    reason_code="ACCEPTED_RECLASS_APPLIED",
                )
            )
            _mark_fact(fact, "CONSUMED", "accepted reclassification applied to effective_category")

    # --- Amount corrections (modify one input; never duplicate) ---
    amount_by_txn: dict[str, list[FactRecord]] = {}
    for fact in _facts_by_kind(accepted_facts, FactKind.AMOUNT_CORRECTION):
        payload = fact.payload
        assert isinstance(payload, AmountCorrectionPayload)
        raw_tid = payload.transaction_id
        if raw_tid is None or raw_tid not in classified_mutable:
            _mark_fact(fact, "UNUSED", "amount correction target missing")
            continue
        amount_by_txn.setdefault(raw_tid, []).append(fact)
    for tid, facts in sorted(amount_by_txn.items()):
        amounts = {
            (f.payload.amount.value, f.payload.amount.currency)  # type: ignore[union-attr]
            for f in facts
        }
        if len(amounts) > 1:
            cid = deterministic_id(
                "txn-conflict", ConflictKind.AMOUNT_CORRECTION_CONFLICT.value, tid
            )
            conflicts.append(
                TaxonomyConflict(
                    conflict_id=cid,
                    kind=ConflictKind.AMOUNT_CORRECTION_CONFLICT,
                    scenario_id=classified_mutable[tid]["scenario_id"],
                    transaction_id=tid,
                    fact_ids=tuple(f.fact_id for f in facts),
                    reason="conflicting amount corrections",
                )
            )
            classified_mutable[tid]["conflict_ids"].append(cid)
            classified_mutable[tid]["unresolved_reasons"].append(UnresolvedReason.FACT_CONFLICT)
            for f in facts:
                _mark_fact(f, "UNUSED", "conflicting amount corrections")
            continue
        fact = facts[0]
        payload = fact.payload
        assert isinstance(payload, AmountCorrectionPayload)
        state = classified_mutable[tid]
        before = state["effective_amount"]
        # Preserve sign from ledger when original negative and correction positive magnitude.
        corrected = payload.amount.value
        if before is not None and before < 0 and corrected > 0:
            corrected = -corrected
        elif before is None:
            # Missing ledger amount: use authoritative signed magnitude as expense when unclear.
            corrected = -abs(corrected) if corrected > 0 else corrected
        state["effective_amount"] = corrected
        state["effective_currency"] = payload.amount.currency
        state["evidence_refs"].extend(list(fact.evidence_span_ids))
        state["unresolved_reasons"] = [
            r
            for r in state["unresolved_reasons"]
            if r not in {UnresolvedReason.AMOUNT_MISSING, UnresolvedReason.AMOUNT_INVALID}
        ]
        for f in facts:
            if f.fact_id not in state["applied_fact_ids"]:
                state["applied_fact_ids"].append(f.fact_id)
            adjustments.append(
                AdjustmentEvent(
                    event_id=deterministic_id(
                        "adj", AdjustmentEventType.AMOUNT_CORRECTION.value, f.fact_id, tid
                    ),
                    event_type=AdjustmentEventType.AMOUNT_CORRECTION,
                    scenario_id=f.scenario_id,
                    fact_id=f.fact_id,
                    transaction_id=tid,
                    before={
                        "effective_amount": str(before) if before is not None else None,
                        "currency": state["original_currency"],
                    },
                    after={
                        "effective_amount": str(corrected),
                        "currency": payload.amount.currency,
                    },
                    evidence_span_ids=f.evidence_span_ids,
                    authority_domain=f.authority_domain.value,
                    reason_code="AMOUNT_CORRECTION_APPLIED",
                )
            )
            _mark_fact(f, "CONSUMED", "amount correction applied once to effective_amount")

    # --- Period facts ---
    period_by_txn: dict[str, list[FactRecord]] = {}
    for fact in _facts_by_kind(accepted_facts, FactKind.TRANSACTION_PERIOD):
        payload = fact.payload
        assert isinstance(payload, TransactionPeriodPayload)
        tid = payload.transaction_id
        if tid not in classified_mutable:
            _mark_fact(fact, "UNUSED", "period fact target missing")
            continue
        period_by_txn.setdefault(tid, []).append(fact)
    for tid, facts in sorted(period_by_txn.items()):
        dispositions = {f.payload.disposition for f in facts}  # type: ignore[union-attr]
        if len(dispositions) > 1:
            cid = deterministic_id("txn-conflict", ConflictKind.PERIOD_CONFLICT.value, tid)
            conflicts.append(
                TaxonomyConflict(
                    conflict_id=cid,
                    kind=ConflictKind.PERIOD_CONFLICT,
                    scenario_id=classified_mutable[tid]["scenario_id"],
                    transaction_id=tid,
                    fact_ids=tuple(f.fact_id for f in facts),
                    reason="contradictory period dispositions",
                )
            )
            classified_mutable[tid]["conflict_ids"].append(cid)
            for f in facts:
                _mark_fact(f, "UNUSED", "period conflict")
            continue
        for fact in facts:
            payload = fact.payload
            assert isinstance(payload, TransactionPeriodPayload)
            state = classified_mutable[tid]
            before = {
                "period_excluded": state["period_excluded"],
                "period_start": state["effective_period_start"].isoformat()
                if state["effective_period_start"]
                else None,
                "period_end": state["effective_period_end"].isoformat()
                if state["effective_period_end"]
                else None,
            }
            if payload.disposition is PeriodDisposition.EXCLUDE_FROM_PERIOD:
                state["period_excluded"] = True
                state["period_membership"] = PeriodMembershipHint.EXCLUDED_FROM_PERIOD
                event_type = AdjustmentEventType.PERIOD_EXCLUSION
            else:
                state["period_excluded"] = False
                state["period_membership"] = PeriodMembershipHint.ASSIGNED_SERVICE_PERIOD
                if payload.service_start is not None:
                    state["effective_period_start"] = payload.service_start
                if payload.service_end is not None:
                    state["effective_period_end"] = payload.service_end
                event_type = AdjustmentEventType.PERIOD_ASSIGNMENT
            state["applied_fact_ids"].append(fact.fact_id)
            state["evidence_refs"].extend(list(fact.evidence_span_ids))
            adjustments.append(
                AdjustmentEvent(
                    event_id=deterministic_id("adj", event_type.value, fact.fact_id, tid),
                    event_type=event_type,
                    scenario_id=fact.scenario_id,
                    fact_id=fact.fact_id,
                    transaction_id=tid,
                    before=before,
                    after={
                        "period_excluded": state["period_excluded"],
                        "period_start": state["effective_period_start"].isoformat()
                        if state["effective_period_start"]
                        else None,
                        "period_end": state["effective_period_end"].isoformat()
                        if state["effective_period_end"]
                        else None,
                        "period_label": payload.period_label,
                    },
                    evidence_span_ids=fact.evidence_span_ids,
                    authority_domain=fact.authority_domain.value,
                    reason_code=event_type.value,
                )
            )
            _mark_fact(fact, "CONSUMED", "period fact applied to effective period fields")

    # --- FX: preserve only; never derive rate ---
    for fact in _facts_by_kind(accepted_facts, FactKind.FX_RATE):
        payload = fact.payload
        assert isinstance(payload, FxRatePayload)
        adjustments.append(
            AdjustmentEvent(
                event_id=deterministic_id(
                    "adj", AdjustmentEventType.FX_SETTLEMENT_REFERENCE.value, fact.fact_id
                ),
                event_type=AdjustmentEventType.FX_SETTLEMENT_REFERENCE,
                scenario_id=fact.scenario_id,
                fact_id=fact.fact_id,
                transaction_id=payload.transaction_id,
                before={},
                after={
                    "from_currency": payload.from_currency,
                    "to_currency": payload.to_currency,
                    "source_amount": (
                        {
                            "value": str(payload.source_amount.value),
                            "currency": payload.source_amount.currency,
                        }
                        if payload.source_amount
                        else None
                    ),
                    "settlement_amount": (
                        {
                            "value": str(payload.settlement_amount.value),
                            "currency": payload.settlement_amount.currency,
                        }
                        if payload.settlement_amount
                        else None
                    ),
                    "explicit_rate": str(payload.explicit_rate)
                    if payload.explicit_rate is not None
                    else None,
                    "rate_source": payload.rate_source.value,
                },
                evidence_span_ids=fact.evidence_span_ids,
                authority_domain=fact.authority_domain.value,
                reason_code="FX_REFERENCE_ONLY_NO_IMPLICIT_RATE",
            )
        )
        _mark_fact(
            fact,
            "DEFERRED_STAGE_6",
            "FX retained as reference; no implicit rate; Stage 6 chooses amount if unambiguous",
        )

    # --- Off-ledger / one-time add-backs as derived inputs ---
    for fact in _facts_by_kind(accepted_facts, FactKind.OFF_LEDGER_AMOUNT):
        payload = fact.payload
        assert isinstance(payload, OffLedgerAmountPayload)
        category = (
            MetricCategory.SEVERANCE_LIABILITY
            if "severance" in (payload.label or "").casefold()
            else MetricCategory.ONE_TIME_ADD_BACKS
        )
        input_id = deterministic_id("derived", fact.fact_id, payload.label or "off_ledger")
        derived_inputs.append(
            DerivedCalculationInput(
                input_id=input_id,
                scenario_id=fact.scenario_id,
                fact_id=fact.fact_id,
                fact_kind=fact.fact_kind.value,
                category=category,
                amount=payload.amount.value,
                currency=payload.amount.currency,
                as_of_date=payload.as_of_date,
                label=payload.label,
                evidence_span_ids=fact.evidence_span_ids,
            )
        )
        adjustments.append(
            AdjustmentEvent(
                event_id=deterministic_id(
                    "adj", AdjustmentEventType.OFF_LEDGER_INPUT.value, fact.fact_id
                ),
                event_type=AdjustmentEventType.OFF_LEDGER_INPUT,
                scenario_id=fact.scenario_id,
                fact_id=fact.fact_id,
                before={},
                after={
                    "derived_input_id": input_id,
                    "amount": str(payload.amount.value),
                    "currency": payload.amount.currency,
                    "label": payload.label,
                },
                evidence_span_ids=fact.evidence_span_ids,
                authority_domain=fact.authority_domain.value,
                reason_code="OFF_LEDGER_DERIVED_INPUT",
            )
        )
        _mark_fact(fact, "CONSUMED", "off-ledger amount emitted as derived calculation input")

    for fact in _facts_by_kind(accepted_facts, FactKind.ONE_TIME_ADD_BACK):
        payload = fact.payload
        assert isinstance(payload, OneTimeAddBackPayload)
        input_id = deterministic_id("derived", fact.fact_id, payload.label)
        derived_inputs.append(
            DerivedCalculationInput(
                input_id=input_id,
                scenario_id=fact.scenario_id,
                fact_id=fact.fact_id,
                fact_kind=fact.fact_kind.value,
                category=MetricCategory.ONE_TIME_ADD_BACKS,
                amount=payload.amount.value,
                currency=payload.amount.currency,
                label=payload.label,
                evidence_span_ids=fact.evidence_span_ids,
            )
        )
        _mark_fact(fact, "CONSUMED", "one-time add-back emitted as derived input")

    # --- Ownership / threshold consumption accounting ---
    for fact in accepted_facts:
        if fact.fact_id in fact_consumption:
            continue
        if fact.fact_kind is FactKind.OWNERSHIP:
            _mark_fact(fact, "CONSUMED", "used to build related-party entity set")
        elif fact.fact_kind is FactKind.RELATED_PARTY_THRESHOLD:
            _mark_fact(fact, "CONSUMED", "threshold comparator >= from source 'or more'")
        elif fact.fact_kind is FactKind.SUBSIDIARY_STATUS:
            _mark_fact(fact, "CONSUMED", "subsidiary/group membership enrichment")
        elif fact.fact_kind is FactKind.TRANSACTION_TREATMENT:
            _mark_fact(
                fact,
                "DEFERRED_STAGE_6",
                "treatment include/exclude is selector/expression-level for Stage 6",
            )
        else:
            _mark_fact(fact, "UNUSED", "no Stage 5F consumer registered")

    # --- Related-party + entity scope enrichment ---
    for tid, state in classified_mutable.items():
        scenario_id = state["scenario_id"]
        if scenario_id is None:
            continue
        decision = resolve_related_party(
            scenario_id=scenario_id,
            counterparty_raw=state["counterparty_raw"],
            qualifying=qualifying,
            has_threshold=scenario_id in threshold_scenarios,
            has_ownership=scenario_id in ownership_scenarios,
        )
        state["related_party_status"] = decision.status
        state["related_party_basis"] = decision.basis
        if decision.fact_ids:
            for fid in decision.fact_ids:
                if fid not in state["applied_fact_ids"]:
                    state["applied_fact_ids"].append(fid)
        if decision.basis is RelatedPartyBasis.AMBIGUOUS_IDENTITY:
            state["unresolved_reasons"].append(UnresolvedReason.COUNTERPARTY_IDENTITY_AMBIGUOUS)
            cid = deterministic_id(
                "txn-conflict", ConflictKind.ENTITY_IDENTITY_AMBIGUOUS.value, tid
            )
            state["conflict_ids"].append(cid)
            conflicts.append(
                TaxonomyConflict(
                    conflict_id=cid,
                    kind=ConflictKind.ENTITY_IDENTITY_AMBIGUOUS,
                    scenario_id=scenario_id,
                    transaction_id=tid,
                    fact_ids=decision.fact_ids,
                    reason="ambiguous counterparty identity match",
                )
            )

        # Entity scope: default borrower; subsidiary fact may upgrade.
        scope = EntityScopeKind.BORROWER
        for fact in subsidiary_by_scenario.get(scenario_id, ()):
            payload = fact.payload
            assert isinstance(payload, SubsidiaryStatusPayload)
            keys = normalize_legal_name_keys(payload.entity_name)
            if keys.identity_key == state["counterparty_identity_key"]:
                if payload.status is SubsidiaryKind.GROUP_MEMBER:
                    scope = EntityScopeKind.GROUP
                else:
                    scope = EntityScopeKind.SUBSIDIARY
                if fact.fact_id not in state["applied_fact_ids"]:
                    state["applied_fact_ids"].append(fact.fact_id)
                break
        if decision.status is RelatedPartyStatus.TRUE:
            # Related-party counterparty scope marker (borrower-level payment to RP).
            state["flags"].append("RELATED_PARTY_COUNTERPARTY")
        # Covenant-level GROUP scope does not rewrite every row; mark when definition needs it.
        state["entity_scope"] = scope

    # Expression-scoped / group selectors: flag CAPEX rows when scenario has GROUP_CAPEX selector.
    group_scenarios = {
        d.scenario_id
        for d in definitions
        if d.scope.scope_kind is ScopeKind.EXPRESSION_SCOPED
        or any(s.group_level or s.category is MetricCategory.GROUP_CAPEX for s in d.selectors)
    }
    for _tid, state in classified_mutable.items():
        if state["scenario_id"] in group_scenarios and state["effective_category"] in {
            MetricCategory.CAPEX,
            MetricCategory.GROUP_CAPEX,
        }:
            state["flags"].append("GROUP_SCOPE")

    # --- Build calculation inputs (scenario-linked classified with amount + category) ---
    calc_inputs: list[CalculationInput] = []
    for tid in sorted(classified_mutable):
        state = classified_mutable[tid]
        if state["scenario_id"] is None:
            continue
        if state["effective_category"] is None:
            potentially = True
            unresolved.append(
                UnresolvedTransaction(
                    transaction_id=tid,
                    scenario_id=state["scenario_id"],
                    reasons=tuple(dict.fromkeys(state["unresolved_reasons"])),
                    potentially_relevant=potentially,
                    description=state["description"],
                    notes="missing effective category",
                )
            )
            continue
        if state["effective_amount"] is None:
            unresolved.append(
                UnresolvedTransaction(
                    transaction_id=tid,
                    scenario_id=state["scenario_id"],
                    reasons=tuple(dict.fromkeys(state["unresolved_reasons"]))
                    or (UnresolvedReason.AMOUNT_MISSING,),
                    potentially_relevant=True,
                    description=state["description"],
                    notes="missing effective amount",
                )
            )
            continue
        if state["classification_status"] is ClassificationStatus.CONFLICT:
            unresolved.append(
                UnresolvedTransaction(
                    transaction_id=tid,
                    scenario_id=state["scenario_id"],
                    reasons=tuple(dict.fromkeys(state["unresolved_reasons"]))
                    or (UnresolvedReason.CATEGORY_CONFLICT,),
                    potentially_relevant=True,
                    description=state["description"],
                )
            )
            continue

        flags = list(state["flags"])
        if state["period_excluded"]:
            flags.append("PERIOD_EXCLUDED")
        if state["rejected_fact_ids"]:
            flags.append("HAS_REJECTED_RECLASSIFICATION")

        calc_inputs.append(
            CalculationInput(
                input_id=deterministic_id("calc", tid, state["effective_category"].value),
                scenario_id=state["scenario_id"],
                source_kind=InputSourceKind.LEDGER_ROW,
                transaction_id=tid,
                category=state["effective_category"],
                amount=state["effective_amount"],
                currency=state["effective_currency"],
                transaction_date=state["original_date"],
                period_start=state["effective_period_start"],
                period_end=state["effective_period_end"],
                period_excluded=state["period_excluded"],
                counterparty=state["counterparty_raw"],
                related_party=state["related_party_status"],
                entity_scope=state["entity_scope"],
                flags=tuple(sorted(set(flags))),
                applied_fact_ids=tuple(dict.fromkeys(state["applied_fact_ids"])),
                rejected_fact_ids=tuple(dict.fromkeys(state["rejected_fact_ids"])),
                provenance_refs=tuple(dict.fromkeys(state["evidence_refs"])),
            )
        )

    # Derived inputs → calculation inputs (deduped by input_id)
    seen_derived = set()
    for derived in sorted(derived_inputs, key=lambda d: d.input_id):
        if derived.input_id in seen_derived:
            continue
        seen_derived.add(derived.input_id)
        calc_inputs.append(
            CalculationInput(
                input_id=deterministic_id("calc-derived", derived.input_id),
                scenario_id=derived.scenario_id,
                source_kind=InputSourceKind.AUTHORITATIVE_FACT,
                derived_input_id=derived.input_id,
                category=derived.category,
                amount=derived.amount,
                currency=derived.currency,
                transaction_date=derived.as_of_date,
                period_start=derived.as_of_date,
                period_end=derived.as_of_date,
                related_party=derived.related_party_status,
                entity_scope=derived.entity_scope,
                flags=("OFF_LEDGER",),
                applied_fact_ids=(derived.fact_id,),
                provenance_refs=derived.evidence_span_ids,
            )
        )

    calc_inputs.sort(key=lambda i: (i.scenario_id, i.input_id))
    derived_inputs_sorted = tuple(sorted(derived_inputs, key=lambda d: d.input_id))
    adjustments_sorted = tuple(sorted(adjustments, key=lambda a: a.event_id))
    conflicts_sorted = tuple(sorted(conflicts, key=lambda c: c.conflict_id))

    # Freeze classified transactions
    from halyk_agent.domain.transaction_taxonomy.models import ClassifiedTransaction

    classified_rows: list[ClassifiedTransaction] = []
    for tid in sorted(classified_mutable):
        state = classified_mutable[tid]
        classified_rows.append(
            ClassifiedTransaction(
                transaction_id=state["transaction_id"],
                source_ledger=state["source_ledger"],
                source_row_index=state["source_row_index"],
                source_sha256=state["source_sha256"],
                scenario_id=state["scenario_id"],
                account_id=state["account_id"],
                original_amount=state["original_amount"],
                original_currency=state["original_currency"],
                effective_amount=state["effective_amount"],
                effective_currency=state["effective_currency"],
                original_category=state["original_category"],
                effective_category=state["effective_category"],
                original_date=state["original_date"],
                effective_period_start=state["effective_period_start"],
                effective_period_end=state["effective_period_end"],
                period_membership=state["period_membership"],
                period_excluded=state["period_excluded"],
                counterparty_raw=state["counterparty_raw"],
                counterparty_identity_key=state["counterparty_identity_key"],
                description=state["description"],
                related_party_status=state["related_party_status"],
                related_party_basis=state["related_party_basis"],
                entity_scope=state["entity_scope"],
                classification_status=state["classification_status"],
                classification_method=state["classification_method"],
                classification_rule=state["classification_rule"],
                applied_fact_ids=tuple(dict.fromkeys(state["applied_fact_ids"])),
                rejected_fact_ids=tuple(dict.fromkeys(state["rejected_fact_ids"])),
                evidence_refs=tuple(dict.fromkeys(state["evidence_refs"])),
                unresolved_reasons=tuple(dict.fromkeys(state["unresolved_reasons"])),
                conflict_ids=tuple(dict.fromkeys(state["conflict_ids"])),
            )
        )

    # Unresolved rows remain potentially_relevant (fail-closed); no silent IRRELEVANT demotion.
    selector_coverage = build_selector_coverage(definitions, tuple(calc_inputs))

    category_counts = Counter(
        c.effective_category.value
        for c in classified_rows
        if c.scenario_id is not None and c.effective_category is not None
    )
    method_counts = Counter(
        c.classification_method.value for c in classified_rows if c.scenario_id is not None
    )

    rp_true = sum(
        1
        for c in classified_rows
        if c.scenario_id and c.related_party_status is RelatedPartyStatus.TRUE
    )
    rp_false = sum(
        1
        for c in classified_rows
        if c.scenario_id and c.related_party_status is RelatedPartyStatus.FALSE
    )
    rp_unknown = sum(
        1
        for c in classified_rows
        if c.scenario_id and c.related_party_status is RelatedPartyStatus.UNKNOWN
    )

    classified_count = sum(
        1
        for c in classified_rows
        if c.scenario_id and c.classification_status is ClassificationStatus.CLASSIFIED
    )
    unresolved_count = sum(
        1
        for c in classified_rows
        if c.scenario_id and c.classification_status is ClassificationStatus.UNRESOLVED
    )
    conflict_count = len(conflicts_sorted)
    noise_count = sum(
        1 for c in classified_rows if c.classification_status is ClassificationStatus.ROUTING_NOISE
    )

    fact_consumption_tuple = tuple(
        sorted(fact_consumption.values(), key=lambda f: (f.scenario_id, f.fact_kind, f.fact_id))
    )
    unresolved_tuple = tuple(
        sorted(unresolved, key=lambda u: (u.scenario_id or "", u.transaction_id))
    )

    taxonomy_hash = _hash_models(list(classified_rows))
    calc_hash = _hash_models(list(calc_inputs))
    adj_hash = _hash_models(list(adjustments_sorted))

    manifest = TaxonomyManifest(
        schema_version=TAXONOMY_SCHEMA_VERSION,
        algorithm_version=TAXONOMY_ALGORITHM_VERSION,
        classifier_version=TAXONOMY_CLASSIFIER_VERSION,
        routing_manifest_hash=routing_manifest_hash,
        covenant_manifest_hash=covenant_manifest_hash,
        facts_manifest_hash=facts_manifest_hash,
        ledger_source_sha256=ledger_source_sha256,
        scenario_count=len({d.scenario_id for d in definitions}),
        ledger_row_count=len(ledger_rows),
        scenario_linked_count=len(scenario_txns),
        routing_noise_count=noise_count,
        classified_count=classified_count,
        unresolved_count=unresolved_count,
        conflict_count=conflict_count,
        irrelevant_count=0,
        calculation_input_count=len(calc_inputs),
        derived_input_count=len(derived_inputs_sorted),
        adjustment_event_count=len(adjustments_sorted),
        selector_count=len(selector_coverage),
        selector_supported_count=sum(1 for s in selector_coverage if s.status == "SUPPORTED"),
        selector_unsupported_count=sum(
            1 for s in selector_coverage if s.status == "UNSUPPORTED_SELECTOR"
        ),
        accepted_facts_count=len(accepted_facts),
        facts_consumed_count=sum(1 for f in fact_consumption_tuple if f.disposition == "CONSUMED"),
        related_party_true_count=rp_true,
        related_party_false_count=rp_false,
        related_party_unknown_count=rp_unknown,
        category_counts=dict(sorted(category_counts.items())),
        method_counts=dict(sorted(method_counts.items())),
        taxonomy_hash=taxonomy_hash,
        calculation_inputs_hash=calc_hash,
        adjustments_hash=adj_hash,
    )

    return TaxonomyReport(
        manifest=manifest,
        classified=tuple(classified_rows),
        adjustments=adjustments_sorted,
        calculation_inputs=tuple(calc_inputs),
        derived_inputs=derived_inputs_sorted,
        conflicts=conflicts_sorted,
        unresolved=unresolved_tuple,
        selector_coverage=selector_coverage,
        fact_consumption=fact_consumption_tuple,
        qualifying_related_parties=tuple(
            {
                "scenario_id": q.scenario_id,
                "entity_name": q.entity_name,
                "identity_key": q.identity_key,
                "ownership_percent": str(q.ownership_percent),
                "threshold_percent": str(q.threshold_percent),
                "fact_ids": list(q.fact_ids),
            }
            for q in qualifying
        ),
    )
