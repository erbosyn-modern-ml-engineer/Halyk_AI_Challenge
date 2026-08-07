"""Scenario discovery tests."""

from __future__ import annotations

import pytest

from halyk_agent.domain.routing.scenarios import ScenarioDiscoveryError, discover_scenarios


def test_scenario_universe_from_template_not_hardcoded() -> None:
    answers = {
        "Z9": {"6.1": None, "6.2": None},
        "A1": {"c1": None},
    }
    scenarios = discover_scenarios(answers)
    assert [s.scenario_id for s in scenarios] == ["A1", "Z9"]
    assert scenarios[0].required_covenant_ids == ("c1",)
    assert scenarios[1].required_covenant_ids == ("6.1", "6.2")


def test_duplicate_scenario_rejected() -> None:
    class DupMap(dict[str, dict[str, object]]):
        def items(self):  # type: ignore[override]
            yield "P1", {"6.1": None}
            yield "P1", {"6.2": None}

        def __bool__(self) -> bool:
            return True

    with pytest.raises(ScenarioDiscoveryError, match="duplicate"):
        discover_scenarios(DupMap({"P1": {"6.1": None}}))


def test_empty_answers_rejected() -> None:
    with pytest.raises(ScenarioDiscoveryError):
        discover_scenarios({})
