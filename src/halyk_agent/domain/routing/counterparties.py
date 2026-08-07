"""Counterparty identity normalization from ledger rows."""

from __future__ import annotations

from dataclasses import dataclass, field

from halyk_agent.domain.routing.models import (
    CounterpartyIdentity,
    TransactionEntityLink,
)
from halyk_agent.domain.routing.normalize import normalize_legal_name


@dataclass
class _CounterpartyBucket:
    normalized: str
    txn_ids: list[str] = field(default_factory=list)
    scenario_ids: set[str] = field(default_factory=set)
    account_ids: set[str] = field(default_factory=set)
    count: int = 0


def build_counterparty_identities(
    links: tuple[TransactionEntityLink, ...],
) -> tuple[CounterpartyIdentity, ...]:
    """Normalize counterparties without fuzzy grouping or role classification."""
    by_raw: dict[str, _CounterpartyBucket] = {}
    for link in links:
        raw = link.counterparty_raw
        normalized, _, _ = normalize_legal_name(raw, strip_suffixes=False)
        bucket = by_raw.get(raw)
        if bucket is None:
            bucket = _CounterpartyBucket(normalized=normalized)
            by_raw[raw] = bucket
        bucket.txn_ids.append(link.txn_id)
        if link.scenario_id:
            bucket.scenario_ids.add(link.scenario_id)
        bucket.account_ids.add(link.account_id_normalized)
        bucket.count += 1

    results: list[CounterpartyIdentity] = []
    for raw, bucket in sorted(by_raw.items(), key=lambda item: item[0]):
        results.append(
            CounterpartyIdentity(
                counterparty_raw=raw,
                counterparty_normalized=bucket.normalized,
                occurrence_count=bucket.count,
                txn_ids=tuple(sorted(set(bucket.txn_ids))),
                scenario_ids=tuple(sorted(bucket.scenario_ids)),
                account_ids=tuple(sorted(bucket.account_ids)),
            )
        )
    return tuple(results)
