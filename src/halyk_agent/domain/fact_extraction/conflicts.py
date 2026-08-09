"""Conflict detection and content-addressed dedupe for accepted facts."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from halyk_agent.domain.fact_extraction.models import (
    AmountCorrectionPayload,
    ContingentObligationPayload,
    FactConflict,
    FactKind,
    FactRecord,
    FactValidatorStatus,
    FxRatePayload,
    GroupCapexPayload,
    GroupFinancialMetricPayload,
    OwnershipPayload,
    RelatedPartyThresholdPayload,
    ScheduledPrincipalPayload,
    SubsidiaryStatusPayload,
    TransactionPeriodPayload,
    TransactionReclassificationPayload,
    TransactionTreatmentPayload,
)
from halyk_agent.domain.ids import deterministic_id, sha256_text
from halyk_agent.domain.routing.normalize import normalize_legal_name_keys


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
    """Collapse byte-equivalent facts by content id."""
    seen: set[str] = set()
    out: list[FactRecord] = []
    for fact in facts:
        fid = content_fact_id(fact)
        if fid in seen:
            continue
        seen.add(fid)
        if fact.fact_id != fid:
            fact = fact.model_copy(update={"fact_id": fid})
        out.append(fact)
    return tuple(out)


def _money_key(amount_value: Decimal | None, currency: str | None) -> str:
    if amount_value is None:
        return "none"
    return f"{amount_value}:{(currency or '').upper()}"


def _entity_key(value: str) -> str:
    keys = normalize_legal_name_keys(value)
    return keys.identity_key or value.strip().casefold()


def _stable_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _conflict_identity_and_value(fact: FactRecord) -> tuple[str, str] | None:
    """Return the semantic assertion key and value for fact kinds that must be unique."""
    payload = fact.payload

    if isinstance(payload, TransactionReclassificationPayload):
        if payload.transaction_id is None:
            return None
        identity = f"txn:{payload.transaction_id}"
        value: dict[str, Any] = {
            "disposition": payload.disposition.value,
            "from": (payload.from_category or "").strip().casefold() or None,
            "to": (payload.to_category or "").strip().casefold() or None,
            "amount": (
                _money_key(payload.amount.value, payload.amount.currency)
                if payload.amount is not None
                else None
            ),
        }
        return identity, _stable_value(value)

    if isinstance(payload, TransactionPeriodPayload):
        identity = f"txn:{payload.transaction_id}"
        value = {
            "disposition": payload.disposition.value,
            "period_label": (payload.period_label or "").strip().casefold() or None,
            "service_start": payload.service_start,
            "service_end": payload.service_end,
        }
        return identity, _stable_value(value)

    if isinstance(payload, AmountCorrectionPayload):
        if payload.transaction_id is None:
            return None
        return (
            f"txn:{payload.transaction_id}",
            _money_key(payload.amount.value, payload.amount.currency),
        )

    if isinstance(payload, OwnershipPayload):
        identity = f"entity:{_entity_key(payload.entity_name)}:{payload.holder_label.casefold()}"
        value = {
            "ownership_percent": payload.ownership_percent,
            "voting_rights": payload.voting_rights,
        }
        return identity, _stable_value(value)

    if isinstance(payload, RelatedPartyThresholdPayload):
        return (
            f"holder:{payload.holder_label.casefold()}",
            _stable_value(payload.threshold_percent),
        )

    if isinstance(payload, SubsidiaryStatusPayload):
        return f"entity:{_entity_key(payload.entity_name)}", payload.status.value

    if isinstance(payload, FxRatePayload):
        # A scenario may legitimately contain several FX observations. Only facts tied to
        # the same ledger transaction are required to agree with each other.
        if payload.transaction_id is None:
            return None
        value = {
            "from": payload.from_currency.upper(),
            "to": payload.to_currency.upper(),
            "explicit_rate": payload.explicit_rate,
            "rate_source": payload.rate_source.value,
            "source_amount": (
                _money_key(payload.source_amount.value, payload.source_amount.currency)
                if payload.source_amount is not None
                else None
            ),
            "settlement_amount": (
                _money_key(payload.settlement_amount.value, payload.settlement_amount.currency)
                if payload.settlement_amount is not None
                else None
            ),
            "as_of_date": payload.as_of_date,
        }
        return f"txn:{payload.transaction_id}", _stable_value(value)

    if isinstance(payload, GroupCapexPayload):
        value = {
            "amount": _money_key(payload.amount.value, payload.amount.currency),
            "period_label": (payload.period_label or "").strip().casefold() or None,
        }
        return "group-capex", _stable_value(value)

    if isinstance(payload, GroupFinancialMetricPayload):
        value = {
            "metric": payload.metric.value,
            "scope": payload.scope.value,
            "amount": _money_key(payload.amount.value, payload.amount.currency),
            "period_label": (payload.period_label or "").strip().casefold() or None,
            "as_of_date": payload.as_of_date,
        }
        return f"group-metric:{payload.metric.value}:{payload.scope.value}", _stable_value(value)

    if isinstance(payload, ContingentObligationPayload):
        # Same type+scope+as_of with different amounts is a conflict; otherwise additive.
        if payload.as_of_date is None and payload.period_label is None:
            return None
        identity = (
            f"contingent:{payload.obligation_type.value}:{payload.scope.value}:"
            f"{payload.as_of_date or (payload.period_label or '').casefold()}"
        )
        return identity, _money_key(payload.amount.value, payload.amount.currency)

    if isinstance(payload, ScheduledPrincipalPayload):
        if payload.transaction_id:
            return (
                f"txn:{payload.transaction_id}",
                _money_key(payload.amount.value, payload.amount.currency),
            )
        if payload.period_label or payload.as_of_date:
            identity = (
                f"scheduled-principal:{payload.scope.value}:"
                f"{payload.as_of_date or (payload.period_label or '').casefold()}"
            )
            return identity, _money_key(payload.amount.value, payload.amount.currency)
        return None

    if isinstance(payload, TransactionTreatmentPayload):
        return f"txn:{payload.transaction_id}", payload.disposition.value

    # OFF_LEDGER_AMOUNT and ONE_TIME_ADD_BACK are additive assertions. Equal labels do
    # not imply that they refer to the same economic item, so they are not collapsed
    # into a uniqueness conflict here.
    return None


def detect_conflicts(facts: tuple[FactRecord, ...]) -> tuple[FactConflict, ...]:
    """Mark incompatible assertions about the same semantic object as conflicts."""
    accepted = [fact for fact in facts if fact.validator_status is FactValidatorStatus.ACCEPTED]
    groups: dict[tuple[FactKind, str, str], list[tuple[FactRecord, str]]] = {}

    for fact in accepted:
        semantic = _conflict_identity_and_value(fact)
        if semantic is None:
            continue
        identity, value = semantic
        key = (fact.fact_kind, fact.scenario_id, identity)
        groups.setdefault(key, []).append((fact, value))

    conflicts: list[FactConflict] = []
    for (kind, scenario_id, identity), group in sorted(
        groups.items(), key=lambda item: (item[0][0].value, item[0][1], item[0][2])
    ):
        values = {value for _fact, value in group}
        if len(values) <= 1:
            continue
        fact_ids = tuple(sorted(content_fact_id(fact) for fact, _value in group))
        conflicts.append(
            FactConflict(
                conflict_id=deterministic_id(
                    "fact-conflict",
                    kind.value,
                    scenario_id,
                    identity,
                    *fact_ids,
                ),
                scenario_id=scenario_id,
                fact_kind=kind,
                fact_ids=fact_ids,
                reason=f"conflicting {kind.value} assertions for {identity}",
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
