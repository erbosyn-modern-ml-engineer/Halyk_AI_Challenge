"""Stage 5F orchestration: classify → adjust → enrich → calculation inputs."""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from halyk_agent.domain.covenants.ast import MetricCategory
from halyk_agent.domain.covenants.models import CovenantDefinition
from halyk_agent.domain.fact_extraction.models import (
    AmountCorrectionPayload,
    FactKind,
    FactRecord,
    FactRequirementResult,
    FxRatePayload,
    GroupCapexPayload,
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
from halyk_agent.domain.transaction_taxonomy.amounts import (
    metric_amount_from_source,
    sign_contract_for_category,
)
from halyk_agent.domain.transaction_taxonomy.category_labels import map_category_label
from halyk_agent.domain.transaction_taxonomy.classify import classify_description
from halyk_agent.domain.transaction_taxonomy.constants import (
    TAXONOMY_ALGORITHM_VERSION,
    TAXONOMY_CLASSIFIER_VERSION,
    TAXONOMY_SCHEMA_VERSION,
)
from halyk_agent.domain.transaction_taxonomy.membership import (
    MEMBERSHIP_REASON_ONE_TIME_ADD_BACK,
    membership_reasons,
    selector_memberships,
)
from halyk_agent.domain.transaction_taxonomy.models import (
    AMOUNT_CONTRACT_VERSION,
    AdjustmentEvent,
    AdjustmentEventType,
    CalculationInput,
    ClassificationMethod,
    ClassificationStatus,
    ConflictKind,
    DerivedCalculationInput,
    EntityScopeKind,
    FactConsumptionEntry,
    InputPeriodSemantics,
    InputSourceKind,
    PeriodMembershipHint,
    RelatedPartyBasis,
    RelatedPartyStatus,
    SelectorReadinessStatus,
    SubsidiaryStatusKind,
    TaxonomyConflict,
    TaxonomyManifest,
    TaxonomyReport,
    UnresolvedReason,
    UnresolvedTransaction,
)
from halyk_agent.domain.transaction_taxonomy.related_party import (
    damaged_qualifying_entities,
    qualifying_related_parties,
    resolve_related_party,
)
from halyk_agent.domain.transaction_taxonomy.selectors import (
    build_definition_readiness,
    build_selector_coverage,
)


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


def hash_taxonomy_models(models: list[Any] | tuple[Any, ...]) -> str:
    """Canonical content hash for Stage 5F artifact integrity (stable ordering assumed)."""
    return _hash_models(list(models))


def _flow_period_for_category(
    definitions: tuple[CovenantDefinition, ...] | list[CovenantDefinition],
    *,
    scenario_id: str,
    category: MetricCategory,
) -> tuple[date | None, date | None]:
    """Unique covenant flow interval bound to ``category`` in ``scenario_id``, else undecidable."""
    periods: set[tuple[date, date]] = set()
    for definition in definitions:
        if definition.scenario_id != scenario_id:
            continue
        if not any(selector.category is category for selector in definition.selectors):
            continue
        start = definition.period.flow_start_date or definition.period.start_date
        end = definition.period.flow_end_date or definition.period.end_date
        if start is not None and end is not None:
            periods.add((start, end))
    if len(periods) == 1:
        return next(iter(periods))
    return None, None


def _as_of_for_category(
    definitions: tuple[CovenantDefinition, ...] | list[CovenantDefinition],
    *,
    scenario_id: str,
    category: MetricCategory,
) -> date | None:
    """Unique covenant as-of date bound to ``category`` in ``scenario_id``, else undecidable."""
    dates: set[date] = set()
    for definition in definitions:
        if definition.scenario_id != scenario_id:
            continue
        if not any(selector.category is category for selector in definition.selectors):
            continue
        if definition.period.as_of_date is not None:
            dates.add(definition.period.as_of_date)
    if len(dates) == 1:
        return next(iter(dates))
    return None


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
    fact_requirement_results: tuple[FactRequirementResult, ...] | None = None,
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
                "subsidiary_status": SubsidiaryStatusKind.UNKNOWN,
                "selector_categories": [],
                "membership_reasons": [],
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
            "subsidiary_status": SubsidiaryStatusKind.UNKNOWN,
            "selector_categories": [],
            "membership_reasons": [],
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
        # Scenario ownership comes exclusively from Stage 5B routing.
        scenario_set = {tid for tid, link in links.items() if link.scenario_id == fact.scenario_id}
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
        as_of = payload.as_of_date
        if as_of is None and category is MetricCategory.SEVERANCE_LIABILITY:
            # Bind unique compiled covenant AS_OF when the fact payload omitted it.
            as_of = _as_of_for_category(
                definitions, scenario_id=fact.scenario_id, category=category
            )
        if category is MetricCategory.SEVERANCE_LIABILITY:
            period_semantics = InputPeriodSemantics.AS_OF
            flow_start, flow_end = None, None
        else:
            period_semantics = InputPeriodSemantics.FLOW
            flow_start, flow_end = _flow_period_for_category(
                definitions, scenario_id=fact.scenario_id, category=category
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
                period_semantics=period_semantics,
                as_of_date=as_of,
                period_start=flow_start,
                period_end=flow_end,
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
        # Prefer unique ledger attachment by amount+counterparty; else fact-derived.
        ledger_matches: list[str] = []
        if payload.counterparty:
            cp_key = normalize_legal_name_keys(payload.counterparty).identity_key
            target_abs = abs(payload.amount.value)
            for tid, state in classified_mutable.items():
                if state["scenario_id"] != fact.scenario_id:
                    continue
                if state["counterparty_identity_key"] != cp_key:
                    continue
                amt = state["effective_amount"]
                if amt is None:
                    continue
                if abs(amt) == target_abs:
                    ledger_matches.append(tid)
        if len(ledger_matches) == 1:
            tid = ledger_matches[0]
            state = classified_mutable[tid]
            # Metric-role augmentation — do NOT replace the expense category.
            state["applied_fact_ids"].append(fact.fact_id)
            state["evidence_refs"].extend(list(fact.evidence_span_ids))
            state["flags"].append("ONE_TIME_ADD_BACK_ATTACHED")
            _mark_fact(fact, "CONSUMED", "one-time add-back attached to unique ledger row")
            continue
        flow_start = payload.period_start
        flow_end = payload.period_end
        if flow_start is None or flow_end is None:
            bound_start, bound_end = _flow_period_for_category(
                definitions,
                scenario_id=fact.scenario_id,
                category=MetricCategory.ONE_TIME_ADD_BACKS,
            )
            flow_start = flow_start or bound_start
            flow_end = flow_end or bound_end
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
                period_semantics=InputPeriodSemantics.FLOW,
                period_start=flow_start,
                period_end=flow_end,
                label=payload.label,
                evidence_span_ids=fact.evidence_span_ids,
            )
        )
        _mark_fact(fact, "CONSUMED", "one-time add-back emitted as derived input")

    for fact in _facts_by_kind(accepted_facts, FactKind.GROUP_CAPEX):
        payload = fact.payload
        assert isinstance(payload, GroupCapexPayload)
        flow_start, flow_end = _flow_period_for_category(
            definitions,
            scenario_id=fact.scenario_id,
            category=MetricCategory.GROUP_CAPEX,
        )
        input_id = deterministic_id("derived", fact.fact_id, "group_capex")
        derived_inputs.append(
            DerivedCalculationInput(
                input_id=input_id,
                scenario_id=fact.scenario_id,
                fact_id=fact.fact_id,
                fact_kind=fact.fact_kind.value,
                category=MetricCategory.GROUP_CAPEX,
                amount=payload.amount.value,
                currency=payload.amount.currency,
                period_semantics=InputPeriodSemantics.FLOW,
                period_start=flow_start,
                period_end=flow_end,
                label=payload.formula or "group_capex",
                entity_scope=EntityScopeKind.GROUP,
                evidence_span_ids=fact.evidence_span_ids,
            )
        )
        _mark_fact(fact, "CONSUMED", "group CAPEX fact emitted as group-level derived input")

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

    damaged_entities = damaged_qualifying_entities(accepted_facts)

    # --- Related-party + entity / subsidiary scope enrichment ---
    for tid, state in classified_mutable.items():
        scenario_id = state["scenario_id"]
        if scenario_id is None:
            state["subsidiary_status"] = SubsidiaryStatusKind.UNKNOWN
            continue
        decision = resolve_related_party(
            scenario_id=scenario_id,
            counterparty_raw=state["counterparty_raw"],
            qualifying=qualifying,
            has_threshold=scenario_id in threshold_scenarios,
            has_ownership=scenario_id in ownership_scenarios,
            damaged_entities=damaged_entities,
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

        # Subsidiary status: UNKNOWN unless trusted fact proves otherwise.
        # Never map UNKNOWN → UNRESTRICTED.
        scope = EntityScopeKind.BORROWER
        sub_status = SubsidiaryStatusKind.UNKNOWN
        for fact in subsidiary_by_scenario.get(scenario_id, ()):
            payload = fact.payload
            assert isinstance(payload, SubsidiaryStatusPayload)
            keys = normalize_legal_name_keys(payload.entity_name)
            if keys.identity_key != state["counterparty_identity_key"]:
                continue
            if payload.status is SubsidiaryKind.UNRESTRICTED:
                sub_status = SubsidiaryStatusKind.UNRESTRICTED
                scope = EntityScopeKind.SUBSIDIARY
            elif payload.status is SubsidiaryKind.RESTRICTED:
                sub_status = SubsidiaryStatusKind.RESTRICTED
                scope = EntityScopeKind.SUBSIDIARY
            elif payload.status is SubsidiaryKind.GROUP_MEMBER:
                sub_status = SubsidiaryStatusKind.GROUP_MEMBER
                scope = EntityScopeKind.GROUP
            if fact.fact_id not in state["applied_fact_ids"]:
                state["applied_fact_ids"].append(fact.fact_id)
            break

        # Promote unrestricted-sub category only with trusted UNRESTRICTED status.
        if (
            state["classification_rule"] == "CAPITAL_TRANSFER_SUBSIDIARY"
            and sub_status is SubsidiaryStatusKind.UNRESTRICTED
        ):
            state["effective_category"] = (
                MetricCategory.CAPITAL_ASSET_TRANSFERS_TO_UNRESTRICTED_SUBS
            )
        elif (
            state["effective_category"]
            is MetricCategory.CAPITAL_ASSET_TRANSFERS_TO_UNRESTRICTED_SUBS
            and sub_status is not SubsidiaryStatusKind.UNRESTRICTED
        ):
            # Fail closed: keep transfer semantics, never invent borrower CAPEX.
            state["effective_category"] = MetricCategory.CAPITAL_ASSET_TRANSFER
            state["flags"].append("UNRESTRICTED_STATUS_REQUIRED")

        # GROUP_CAPEX primary from description only; never stamp borrower CAPEX as group.
        if (
            state["effective_category"] is MetricCategory.GROUP_CAPEX
            and state["classification_rule"] == "GROUP_CAPEX"
        ):
            scope = EntityScopeKind.GROUP
            state["flags"].append("GROUP_LEVEL_SOURCE")

        if decision.status is RelatedPartyStatus.TRUE:
            state["flags"].append("RELATED_PARTY_COUNTERPARTY")
        state["entity_scope"] = scope
        state["subsidiary_status"] = sub_status

        # Attach semantic memberships (primary + hierarchy; tax is row-level).
        if state["effective_category"] is not None:
            desc = state["description"] or ""
            cats = list(selector_memberships(state["effective_category"], description=desc))
            reason_codes = list(membership_reasons(state["effective_category"], description=desc))
            # ONE_TIME_ADD_BACK is an additional metric membership, not a reclassification.
            if "ONE_TIME_ADD_BACK_ATTACHED" in state["flags"]:
                if MetricCategory.ONE_TIME_ADD_BACKS not in cats:
                    cats.append(MetricCategory.ONE_TIME_ADD_BACKS)
                if MEMBERSHIP_REASON_ONE_TIME_ADD_BACK not in reason_codes:
                    reason_codes.append(MEMBERSHIP_REASON_ONE_TIME_ADD_BACK)
            state["selector_categories"] = cats
            state["membership_reasons"] = reason_codes
        else:
            state["selector_categories"] = []
            state["membership_reasons"] = []

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

        desc = state["description"] or ""
        selector_cats = tuple(state.get("selector_categories") or [])
        if not selector_cats:
            selector_cats = selector_memberships(state["effective_category"], description=desc)
        mem_reasons = tuple(state.get("membership_reasons") or [])
        if not mem_reasons:
            mem_reasons = membership_reasons(state["effective_category"], description=desc)

        source_amt = state["effective_amount"]
        assert source_amt is not None
        semantics, sign_rule = sign_contract_for_category(state["effective_category"])
        metric_amt = metric_amount_from_source(source_amt, category=state["effective_category"])

        calc_inputs.append(
            CalculationInput(
                input_id=deterministic_id("calc", tid, state["effective_category"].value),
                scenario_id=state["scenario_id"],
                source_kind=InputSourceKind.LEDGER_ROW,
                transaction_id=tid,
                category=state["effective_category"],
                selector_categories=selector_cats,
                membership_reasons=mem_reasons,
                amount=metric_amt,
                source_amount=source_amt,
                metric_amount=metric_amt,
                amount_semantics=semantics,
                sign_rule=sign_rule,
                currency=state["effective_currency"],
                period_semantics=InputPeriodSemantics.FLOW,
                transaction_date=state["original_date"],
                period_start=state["effective_period_start"],
                period_end=state["effective_period_end"],
                period_excluded=state["period_excluded"],
                counterparty=state["counterparty_raw"],
                related_party=state["related_party_status"],
                entity_scope=state["entity_scope"],
                subsidiary_status=state.get("subsidiary_status", SubsidiaryStatusKind.UNKNOWN),
                flags=tuple(sorted(set(flags))),
                applied_fact_ids=tuple(dict.fromkeys(state["applied_fact_ids"])),
                rejected_fact_ids=tuple(dict.fromkeys(state["rejected_fact_ids"])),
                provenance_refs=tuple(dict.fromkeys(state["evidence_refs"])),
                classification_rule=state.get("classification_rule"),
            )
        )

    # Derived inputs → calculation inputs (deduped by input_id)
    seen_derived = set()
    for derived in sorted(derived_inputs, key=lambda d: d.input_id):
        if derived.input_id in seen_derived:
            continue
        seen_derived.add(derived.input_id)
        positive_magnitude = True
        semantics, sign_rule = sign_contract_for_category(
            derived.category, positive_magnitude=positive_magnitude
        )
        metric_amt = metric_amount_from_source(
            derived.amount,
            category=derived.category,
            positive_magnitude=positive_magnitude,
        )
        derived_flags: list[str] = ["OFF_LEDGER"]
        if derived.category is MetricCategory.GROUP_CAPEX:
            derived_flags.append("GROUP_LEVEL_SOURCE")
        if derived.period_semantics is InputPeriodSemantics.AS_OF:
            txn_date = derived.as_of_date
            p_start = None
            p_end = None
            as_of = derived.as_of_date
        else:
            txn_date = None
            p_start = derived.period_start
            p_end = derived.period_end
            as_of = None
        calc_inputs.append(
            CalculationInput(
                input_id=deterministic_id("calc-derived", derived.input_id),
                scenario_id=derived.scenario_id,
                source_kind=InputSourceKind.AUTHORITATIVE_FACT,
                derived_input_id=derived.input_id,
                category=derived.category,
                selector_categories=selector_memberships(derived.category),
                membership_reasons=membership_reasons(derived.category),
                amount=metric_amt,
                source_amount=derived.amount,
                metric_amount=metric_amt,
                amount_semantics=semantics,
                sign_rule=sign_rule,
                currency=derived.currency,
                period_semantics=derived.period_semantics,
                transaction_date=txn_date,
                period_start=p_start,
                period_end=p_end,
                as_of_date=as_of,
                related_party=derived.related_party_status,
                entity_scope=derived.entity_scope,
                subsidiary_status=SubsidiaryStatusKind.UNKNOWN,
                flags=tuple(sorted(set(derived_flags))),
                applied_fact_ids=(derived.fact_id,),
                provenance_refs=derived.evidence_span_ids,
                classification_rule="AUTHORITATIVE_FACT",
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
                subsidiary_status=state.get("subsidiary_status", SubsidiaryStatusKind.UNKNOWN),
                selector_categories=tuple(state.get("selector_categories") or ()),
                membership_reasons=tuple(state.get("membership_reasons") or ()),
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
    group_capex_scenarios = {
        d.scenario_id
        for d in definitions
        if any(s.category is MetricCategory.GROUP_CAPEX for s in d.selectors)
    }
    group_capex_unresolved = {
        sid
        for sid in group_capex_scenarios
        if not any(
            i.scenario_id == sid
            and MetricCategory.GROUP_CAPEX in (i.selector_categories or (i.category,))
            and "GROUP_LEVEL_SOURCE" in i.flags
            for i in calc_inputs
        )
    }

    unrestricted_scenarios = {
        d.scenario_id
        for d in definitions
        if any(
            s.category is MetricCategory.CAPITAL_ASSET_TRANSFERS_TO_UNRESTRICTED_SUBS
            for s in d.selectors
        )
    }
    unrestricted_unresolved = set()
    for sid in unrestricted_scenarios:
        has_unrestricted_fact = any(
            f.scenario_id == sid
            and f.fact_kind is FactKind.SUBSIDIARY_STATUS
            and isinstance(f.payload, SubsidiaryStatusPayload)
            and f.payload.status is SubsidiaryKind.UNRESTRICTED
            for f in accepted_facts
        )
        if not has_unrestricted_fact:
            unrestricted_unresolved.add(sid)

    selector_coverage = build_selector_coverage(
        definitions,
        tuple(calc_inputs),
        group_capex_unresolved=group_capex_unresolved,
        unrestricted_unresolved=unrestricted_unresolved,
        requirement_results=fact_requirement_results,
    )
    definition_readiness = build_definition_readiness(definitions, selector_coverage)

    category_counts = Counter(
        c.effective_category.value
        for c in classified_rows
        if c.scenario_id is not None and c.effective_category is not None
    )
    membership_counts: Counter[str] = Counter()
    for inp in calc_inputs:
        for cat in inp.selector_categories or (inp.category,):
            membership_counts[cat.value] += 1
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
    selector_coverage_hash = _hash_models(list(selector_coverage))
    definition_readiness_hash = _hash_models(list(definition_readiness))

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
        selector_ready_count=sum(
            1 for s in selector_coverage if s.status is SelectorReadinessStatus.READY
        ),
        selector_true_zero_count=sum(
            1 for s in selector_coverage if s.status is SelectorReadinessStatus.TRUE_ZERO
        ),
        selector_unresolved_count=sum(
            1 for s in selector_coverage if s.status is SelectorReadinessStatus.UNRESOLVED
        ),
        # Legacy aliases: supported ≈ ready+true_zero; unsupported ≈ unresolved.
        selector_supported_count=sum(
            1
            for s in selector_coverage
            if s.status in {SelectorReadinessStatus.READY, SelectorReadinessStatus.TRUE_ZERO}
        ),
        selector_unsupported_count=sum(
            1 for s in selector_coverage if s.status is SelectorReadinessStatus.UNRESOLVED
        ),
        definition_ready_count=sum(
            1 for d in definition_readiness if d.status is SelectorReadinessStatus.READY
        ),
        definition_unresolved_count=sum(
            1 for d in definition_readiness if d.status is SelectorReadinessStatus.UNRESOLVED
        ),
        amount_contract_version=AMOUNT_CONTRACT_VERSION,
        accepted_facts_count=len(accepted_facts),
        facts_consumed_count=sum(1 for f in fact_consumption_tuple if f.disposition == "CONSUMED"),
        related_party_true_count=rp_true,
        related_party_false_count=rp_false,
        related_party_unknown_count=rp_unknown,
        category_counts=dict(sorted(category_counts.items())),
        membership_counts=dict(sorted(membership_counts.items())),
        method_counts=dict(sorted(method_counts.items())),
        taxonomy_hash=taxonomy_hash,
        calculation_inputs_hash=calc_hash,
        adjustments_hash=adj_hash,
        selector_coverage_hash=selector_coverage_hash,
        definition_readiness_hash=definition_readiness_hash,
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
        definition_readiness=definition_readiness,
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
