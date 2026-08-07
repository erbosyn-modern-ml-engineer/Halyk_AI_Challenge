"""Conflict detection and content-addressed dedupe for accepted facts."""

from __future__ import annotations

import json
from decimal import Decimal

from halyk_agent.domain.fact_extraction.models import (
    FactConflict,
    FactKind,
    FactRecord,
    FactValidatorStatus,
    OwnershipPayload,
    TransactionReclassificationPayload,
)
from halyk_agent.domain.ids import deterministic_id, sha256_text


def content_fact_id(record: FactRecord) -> str:
    """Stable content-addressed id over scenario/kind/payload/source."""
    payload_json = json.dumps(
        record.payload.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return deterministic_id(
        "fact",
        record.scenario_id,
        record.fact_kind.value,
        record.authority_domain.value,
        record.source_document_id,
        payload_json,
    )


def dedupe_facts(facts: tuple[FactRecord, ...]) -> tuple[FactRecord, ...]:
    """Collapse equivalent facts by content-addressed fact_id (first wins)."""
    seen: set[str] = set()
    out: list[FactRecord] = []
    for fact in facts:
        fid = content_fact_id(fact)
        if fid in seen:
            continue
        seen.add(fid)
        # Normalize id to content address for stability.
        if fact.fact_id != fid:
            fact = fact.model_copy(update={"fact_id": fid})
        out.append(fact)
    return tuple(out)


def _money_key(amount_value: Decimal | None, currency: str | None) -> str:
    if amount_value is None:
        return "none"
    return f"{amount_value}:{currency or ''}"


def detect_conflicts(facts: tuple[FactRecord, ...]) -> tuple[FactConflict, ...]:
    """
    Detect conflicting accepted facts.

    - Same txn two different amounts (reclassification)
    - Same entity two different ownership percentages
    """
    accepted = [f for f in facts if f.validator_status is FactValidatorStatus.ACCEPTED]
    conflicts: list[FactConflict] = []

    # Reclass / amount by transaction_id
    by_txn: dict[tuple[str, str], list[FactRecord]] = {}
    for fact in accepted:
        if fact.fact_kind is not FactKind.TRANSACTION_RECLASSIFICATION:
            continue
        payload = fact.payload
        if not isinstance(payload, TransactionReclassificationPayload):
            continue
        if payload.transaction_id is None or payload.amount is None:
            continue
        key = (fact.scenario_id, payload.transaction_id)
        by_txn.setdefault(key, []).append(fact)

    for (scenario_id, txn_id), group in sorted(by_txn.items()):
        amounts = {
            _money_key(
                f.payload.amount.value
                if isinstance(f.payload, TransactionReclassificationPayload) and f.payload.amount
                else None,
                f.payload.amount.currency
                if isinstance(f.payload, TransactionReclassificationPayload) and f.payload.amount
                else None,
            )
            for f in group
        }
        if len(amounts) > 1:
            fact_ids = tuple(sorted(content_fact_id(f) for f in group))
            conflicts.append(
                FactConflict(
                    conflict_id=deterministic_id("fact-conflict", scenario_id, txn_id, *fact_ids),
                    scenario_id=scenario_id,
                    fact_kind=FactKind.TRANSACTION_RECLASSIFICATION,
                    fact_ids=fact_ids,
                    reason=f"conflicting amounts for {txn_id}",
                )
            )

    # Ownership by entity
    by_entity: dict[tuple[str, str], list[FactRecord]] = {}
    for fact in accepted:
        if fact.fact_kind is not FactKind.OWNERSHIP:
            continue
        payload = fact.payload
        if not isinstance(payload, OwnershipPayload):
            continue
        key = (fact.scenario_id, payload.entity_name.casefold())
        by_entity.setdefault(key, []).append(fact)

    for (scenario_id, entity_key), group in sorted(by_entity.items()):
        percents = {
            f.payload.ownership_percent for f in group if isinstance(f.payload, OwnershipPayload)
        }
        if len(percents) > 1:
            fact_ids = tuple(sorted(content_fact_id(f) for f in group))
            conflicts.append(
                FactConflict(
                    conflict_id=deterministic_id(
                        "fact-conflict-own",
                        scenario_id,
                        entity_key,
                        *fact_ids,
                    ),
                    scenario_id=scenario_id,
                    fact_kind=FactKind.OWNERSHIP,
                    fact_ids=fact_ids,
                    reason=f"conflicting ownership for {entity_key}",
                )
            )

    conflicts.sort(key=lambda item: item.conflict_id)
    return tuple(conflicts)


def apply_conflicts(
    facts: tuple[FactRecord, ...],
    conflicts: tuple[FactConflict, ...],
) -> tuple[FactRecord, ...]:
    """Mark facts involved in conflicts as CONFLICT status."""
    conflicted: set[str] = set()
    for conflict in conflicts:
        conflicted.update(conflict.fact_ids)
    out: list[FactRecord] = []
    for fact in facts:
        fid = content_fact_id(fact)
        if fid in conflicted and fact.validator_status is FactValidatorStatus.ACCEPTED:
            out.append(
                fact.model_copy(
                    update={
                        "fact_id": fid,
                        "validator_status": FactValidatorStatus.CONFLICT,
                        "reason_code": "CONFLICTING_FACTS",
                    }
                )
            )
        else:
            if fact.fact_id != fid:
                fact = fact.model_copy(update={"fact_id": fid})
            out.append(fact)
    return tuple(out)


def stable_payload_hash(record: FactRecord) -> str:
    return sha256_text(
        json.dumps(record.payload.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    )
