"""Scenario universe discovery from the submission template."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from halyk_agent.domain.routing.models import ScenarioIdentity


class ScenarioDiscoveryError(ValueError):
    """Structural template problem that prevents routing."""


def discover_scenarios(template_answers: Mapping[str, Any]) -> tuple[ScenarioIdentity, ...]:
    """
    Build the authoritative scenario universe from submission template answers.

    Scenario IDs are opaque — never invented from ledger contents.
    Ordering is deterministic (lexicographic by scenario_id).
    """
    if not isinstance(template_answers, Mapping) or not template_answers:
        raise ScenarioDiscoveryError("submission template answers must be a non-empty mapping")

    seen: set[str] = set()
    pending: list[tuple[str, tuple[str, ...]]] = []
    for scenario_id, covenants in template_answers.items():
        sid = str(scenario_id).strip()
        if not sid:
            raise ScenarioDiscoveryError("scenario identifier must be non-empty")
        if sid in seen:
            raise ScenarioDiscoveryError(f"duplicate scenario identifier: {sid}")
        seen.add(sid)
        if not isinstance(covenants, Mapping) or not covenants:
            raise ScenarioDiscoveryError(f"scenario {sid} has no required covenant cells")
        covenant_ids = tuple(sorted(str(key).strip() for key in covenants))
        if any(not cid for cid in covenant_ids):
            raise ScenarioDiscoveryError(f"scenario {sid} has empty covenant identifier")
        if len(set(covenant_ids)) != len(covenant_ids):
            raise ScenarioDiscoveryError(f"scenario {sid} has duplicate covenant identifiers")
        pending.append((sid, covenant_ids))

    pending.sort(key=lambda item: item[0])
    return tuple(
        ScenarioIdentity(
            scenario_id=sid,
            required_covenant_ids=covenant_ids,
            ordinal=index,
        )
        for index, (sid, covenant_ids) in enumerate(pending)
    )


def scenario_universe(scenarios: tuple[ScenarioIdentity, ...]) -> frozenset[str]:
    return frozenset(item.scenario_id for item in scenarios)


def template_cell_count(scenarios: tuple[ScenarioIdentity, ...]) -> int:
    return sum(len(item.required_covenant_ids) for item in scenarios)
