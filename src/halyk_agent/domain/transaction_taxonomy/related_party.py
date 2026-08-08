"""Related-party resolution from Stage 5E ownership + threshold facts."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from halyk_agent.domain.fact_extraction.models import (
    FactKind,
    FactRecord,
    OwnershipPayload,
    RelatedPartyThresholdPayload,
)
from halyk_agent.domain.routing.normalize import normalize_legal_name_keys
from halyk_agent.domain.transaction_taxonomy.models import (
    RelatedPartyBasis,
    RelatedPartyStatus,
)


@dataclass(frozen=True, slots=True)
class QualifyingRelatedParty:
    scenario_id: str
    entity_name: str
    identity_key: str
    ownership_percent: Decimal
    threshold_percent: Decimal
    fact_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DamagedOwnershipEntity:
    scenario_id: str
    entity_name: str
    ownership_percent: Decimal
    threshold_percent: Decimal
    fact_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RelatedPartyDecision:
    status: RelatedPartyStatus
    basis: RelatedPartyBasis
    matched_entity: str | None = None
    fact_ids: tuple[str, ...] = ()


def is_damaged_entity_name(name: str) -> bool:
    """True when OCR/extraction damage makes identity unsafe to match."""
    if not name:
        return True
    return "?" in name or "\ufffd" in name or "�" in name


def _threshold_for_scenario(
    facts: tuple[FactRecord, ...], scenario_id: str
) -> tuple[Decimal | None, str | None]:
    hits = [
        f
        for f in facts
        if f.scenario_id == scenario_id and f.fact_kind is FactKind.RELATED_PARTY_THRESHOLD
    ]
    if not hits:
        return None, None
    payload = hits[0].payload
    assert isinstance(payload, RelatedPartyThresholdPayload)
    return payload.threshold_percent, hits[0].fact_id


def scenario_has_damaged_ownership(facts: tuple[FactRecord, ...], scenario_id: str) -> bool:
    for fact in facts:
        if fact.scenario_id != scenario_id or fact.fact_kind is not FactKind.OWNERSHIP:
            continue
        payload = fact.payload
        assert isinstance(payload, OwnershipPayload)
        if is_damaged_entity_name(payload.entity_name):
            return True
    return False


def damaged_qualifying_entities(
    facts: tuple[FactRecord, ...],
) -> tuple[DamagedOwnershipEntity, ...]:
    """Ownership rows that meet threshold but have damaged names (not matchable exactly)."""
    by_scenario: dict[str, list[FactRecord]] = {}
    for fact in facts:
        by_scenario.setdefault(fact.scenario_id, []).append(fact)
    out: list[DamagedOwnershipEntity] = []
    for scenario_id, scenario_facts in sorted(by_scenario.items()):
        threshold, thr_fact_id = _threshold_for_scenario(tuple(scenario_facts), scenario_id)
        if threshold is None or thr_fact_id is None:
            continue
        for fact in scenario_facts:
            if fact.fact_kind is not FactKind.OWNERSHIP:
                continue
            payload = fact.payload
            assert isinstance(payload, OwnershipPayload)
            if not is_damaged_entity_name(payload.entity_name):
                continue
            if payload.ownership_percent >= threshold:
                out.append(
                    DamagedOwnershipEntity(
                        scenario_id=scenario_id,
                        entity_name=payload.entity_name,
                        ownership_percent=payload.ownership_percent,
                        threshold_percent=threshold,
                        fact_ids=(thr_fact_id, fact.fact_id),
                    )
                )
    return tuple(out)


def possible_damaged_identity_match(damaged_name: str, candidate_name: str) -> bool:
    """
    Narrow wildcard match for explicitly corrupted tokens only.

    Not edit-distance: legal form and every non-damaged token must match exactly;
    only tokens containing corruption markers may vary.
    """
    if not is_damaged_entity_name(damaged_name):
        return False
    left = normalize_legal_name_keys(damaged_name)
    right = normalize_legal_name_keys(candidate_name)
    if left.legal_form is None or right.legal_form is None:
        return False
    if left.legal_form != right.legal_form:
        return False
    left_tokens = left.identity_key.split()
    right_tokens = right.identity_key.split()
    if len(left_tokens) != len(right_tokens):
        return False
    # Drop trailing legal-form token for structural compare (already checked).
    if left_tokens[-1] == left.legal_form and right_tokens[-1] == right.legal_form:
        left_tokens = left_tokens[:-1]
        right_tokens = right_tokens[:-1]
    if len(left_tokens) != len(right_tokens) or not left_tokens:
        return False
    damaged_positions = 0
    for lt, rt in zip(left_tokens, right_tokens, strict=True):
        if "?" in lt or "\ufffd" in lt or "�" in lt:
            damaged_positions += 1
            continue
        if lt != rt:
            return False
    return damaged_positions >= 1


def qualifying_related_parties(
    facts: tuple[FactRecord, ...],
) -> tuple[QualifyingRelatedParty, ...]:
    """
    Entities whose ownership meets scenario threshold.

    Comparator from Stage 5E source wording: \"X% or more\" / \"и более\" → >= .
    Damaged entity names are excluded from the matchable qualifying set.
    """
    by_scenario: dict[str, list[FactRecord]] = {}
    for fact in facts:
        by_scenario.setdefault(fact.scenario_id, []).append(fact)

    out: list[QualifyingRelatedParty] = []
    for scenario_id, scenario_facts in sorted(by_scenario.items()):
        threshold, thr_fact_id = _threshold_for_scenario(tuple(scenario_facts), scenario_id)
        if threshold is None or thr_fact_id is None:
            continue
        for fact in scenario_facts:
            if fact.fact_kind is not FactKind.OWNERSHIP:
                continue
            payload = fact.payload
            assert isinstance(payload, OwnershipPayload)
            if is_damaged_entity_name(payload.entity_name):
                continue
            if payload.ownership_percent >= threshold:
                keys = normalize_legal_name_keys(payload.entity_name)
                out.append(
                    QualifyingRelatedParty(
                        scenario_id=scenario_id,
                        entity_name=payload.entity_name,
                        identity_key=keys.identity_key,
                        ownership_percent=payload.ownership_percent,
                        threshold_percent=threshold,
                        fact_ids=(thr_fact_id, fact.fact_id),
                    )
                )
    out.sort(key=lambda item: (item.scenario_id, item.identity_key, item.entity_name))
    return tuple(out)


def resolve_related_party(
    *,
    scenario_id: str | None,
    counterparty_raw: str,
    qualifying: tuple[QualifyingRelatedParty, ...],
    has_threshold: bool,
    has_ownership: bool,
    damaged_entities: tuple[DamagedOwnershipEntity, ...] = (),
) -> RelatedPartyDecision:
    """Exact identity_key match only — never JSC==LLP fuzzy."""
    if scenario_id is None:
        return RelatedPartyDecision(
            status=RelatedPartyStatus.UNKNOWN,
            basis=RelatedPartyBasis.ROUTING_NOISE,
        )
    if not has_threshold:
        return RelatedPartyDecision(
            status=RelatedPartyStatus.UNKNOWN,
            basis=RelatedPartyBasis.MISSING_THRESHOLD,
        )
    if not has_ownership:
        return RelatedPartyDecision(
            status=RelatedPartyStatus.UNKNOWN,
            basis=RelatedPartyBasis.MISSING_OWNERSHIP,
        )

    cp_key = normalize_legal_name_keys(counterparty_raw).identity_key
    matches = [q for q in qualifying if q.scenario_id == scenario_id and q.identity_key == cp_key]
    if len(matches) > 1:
        names = {m.entity_name for m in matches}
        if len(names) > 1:
            return RelatedPartyDecision(
                status=RelatedPartyStatus.UNKNOWN,
                basis=RelatedPartyBasis.AMBIGUOUS_IDENTITY,
                fact_ids=tuple(fid for m in matches for fid in m.fact_ids),
            )
    if len(matches) == 1:
        m = matches[0]
        return RelatedPartyDecision(
            status=RelatedPartyStatus.TRUE,
            basis=RelatedPartyBasis.OWNERSHIP_THRESHOLD,
            matched_entity=m.entity_name,
            fact_ids=m.fact_ids,
        )

    # Narrow UNKNOWN: only when counterparty could be the damaged qualifying entity.
    for damaged in damaged_entities:
        if damaged.scenario_id != scenario_id:
            continue
        if possible_damaged_identity_match(damaged.entity_name, counterparty_raw):
            return RelatedPartyDecision(
                status=RelatedPartyStatus.UNKNOWN,
                basis=RelatedPartyBasis.DAMAGED_OWNERSHIP_IDENTITY,
                fact_ids=damaged.fact_ids,
            )

    return RelatedPartyDecision(
        status=RelatedPartyStatus.FALSE,
        basis=RelatedPartyBasis.NO_IDENTITY_MATCH,
    )
