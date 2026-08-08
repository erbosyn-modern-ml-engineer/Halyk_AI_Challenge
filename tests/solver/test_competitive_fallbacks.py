"""Adversarial contracts for bounded Stage 8 competition fallbacks."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from tests.covenant_evaluation._helpers import _definition, _selector

from halyk_agent.domain.covenant_evaluation import plan_definition
from halyk_agent.domain.covenants.ast import MetricCategory, Sum, TransactionSet
from halyk_agent.domain.transaction_taxonomy.models import AdjustmentEvent, AdjustmentEventType
from halyk_agent.solver.fallbacks import (
    _derive_group_capex,
    _settlement_eur_usd_rate,
    _unique_group_capex_plan,
)


def _fx(event_id: str, source: str, settlement: str) -> AdjustmentEvent:
    return AdjustmentEvent(
        event_id=event_id,
        event_type=AdjustmentEventType.FX_SETTLEMENT_REFERENCE,
        scenario_id="P3",
        fact_id=f"fact-{event_id}",
        after={
            "source_amount": {"currency": "EUR", "value": source},
            "settlement_amount": {"currency": "USD", "value": settlement},
        },
        evidence_span_ids=(f"span-{event_id}",),
        reason_code="FX_REFERENCE_ONLY_NO_IMPLICIT_RATE",
    )


def test_settlement_rate_requires_one_unique_source_backed_ratio() -> None:
    rate, evidence = _settlement_eur_usd_rate((_fx("a", "72146.75", "83690.23"),))
    assert rate == Decimal("1.16")
    assert evidence == ("span-a",)

    rate, evidence = _settlement_eur_usd_rate(
        (
            _fx("a", "100", "116"),
            _fx("b", "100", "117"),
        )
    )
    assert rate is None
    assert evidence == ()


def _write_p5_bundle(root: Path, note: str, *, scenario_id: str = "P5") -> tuple[Path, Path]:
    parsed = root / "parsed"
    routing = root / "routing"
    (parsed / "documents").mkdir(parents=True)
    routing.mkdir(parents=True)
    document = {
        "document_id": "group-doc",
        "source_file": "documents/group.pdf",
        "pages": [{"text": note}],
    }
    (parsed / "documents" / "group.json").write_text(json.dumps(document), encoding="utf-8")
    (routing / "document_links.jsonl").write_text(
        json.dumps(
            {
                "document_id": "group-doc",
                "scenario_ids": [scenario_id],
                "group_document": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return parsed, routing


def test_p5_ppe_residual_bridge_is_bounded_and_deterministic(tmp_path: Path) -> None:
    note = """Note 7 — Property, Plant and Equipment
There were no disposals of property, plant and equipment during the year.
Net book value at the beginning of the year $148,028,989.69
Depreciation charge for the year $15,826,229.43
Net book value at the end of the year $154,050,122.81
"""
    parsed, routing = _write_p5_bundle(tmp_path, note)
    value, diagnostic = _derive_group_capex(
        parsed_dir=parsed, routing_dir=routing, scenario_id="P5"
    )
    assert value == Decimal("21847362.55")
    assert diagnostic is not None
    assert diagnostic["strategy"] == "PPE_ROLL_FORWARD_RESIDUAL_BRIDGE"


def test_p5_ppe_bridge_rejects_named_competing_movements(tmp_path: Path) -> None:
    note = """Note 7 — Property, Plant and Equipment
There were no disposals of property, plant and equipment during the year.
Revaluation movement was recorded during the year.
Net book value at the beginning of the year $148,028,989.69
Depreciation charge for the year $15,826,229.43
Net book value at the end of the year $154,050,122.81
"""
    parsed, routing = _write_p5_bundle(tmp_path, note)
    value, diagnostic = _derive_group_capex(
        parsed_dir=parsed, routing_dir=routing, scenario_id="P5"
    )
    assert value is None
    assert diagnostic is None


@pytest.mark.parametrize(
    "movement",
    [
        "Acquisitions were recorded during the year.",
        "Transfers in were recorded during the year.",
        "Revaluations were recorded during the year.",
        "Impairments were recorded during the year.",
        "FX movements were recorded during the year.",
        "Currency translation differences were recorded during the year.",
        "Business combination activity occurred during the year.",
        "Assets held for sale movement occurred during the year.",
        "Write-offs were recorded during the year.",
        "Reclassifications were recorded during the year.",
        "Government grants affected PPE during the year.",
        "Capitalised borrowing costs affected PPE during the year.",
        "Right-of-use movement affected PPE during the year.",
    ],
)
def test_p5_bridge_closes_on_plural_and_synonym_competing_movements(
    tmp_path: Path, movement: str
) -> None:
    note = f"""Note 7 — Property, Plant and Equipment
There were no disposals of property, plant and equipment during the year.
Net book value at the beginning of the year $5,000,000
Depreciation charge for the year $400,000
Net book value at the end of the year $5,600,000
{movement}
"""
    parsed, routing = _write_p5_bundle(tmp_path, note)
    assert _derive_group_capex(parsed_dir=parsed, routing_dir=routing, scenario_id="P5") == (
        None,
        None,
    )


def test_p5_bridge_scans_complete_note_not_fixed_character_window(tmp_path: Path) -> None:
    note = (
        """Note 7 — Property, Plant and Equipment
There were no disposals of property, plant and equipment during the year.
Net book value at the beginning of the year $5,000,000
Depreciation charge for the year $400,000
Net book value at the end of the year $5,600,000
"""
        + ("narrative " * 400)
        + "\nRevaluations were recorded.\nNote 8 — Other\n"
    )
    parsed, routing = _write_p5_bundle(tmp_path, note)
    assert _derive_group_capex(parsed_dir=parsed, routing_dir=routing, scenario_id="P5") == (
        None,
        None,
    )


def test_p5_bridge_accepts_complete_unseparated_money_without_truncation(tmp_path: Path) -> None:
    note = """Note 7 — Property, Plant and Equipment
There were no disposals of property, plant and equipment during the year.
Net book value at the beginning of the year $5000000
Depreciation charge for the year $400000
Net book value at the end of the year $5600000
"""
    parsed, routing = _write_p5_bundle(tmp_path, note)
    value, diagnostic = _derive_group_capex(
        parsed_dir=parsed, routing_dir=routing, scenario_id="P5"
    )
    assert value == Decimal("1000000")
    assert diagnostic is not None


def test_p5_bridge_rejects_ocr_corrupt_money(tmp_path: Path) -> None:
    note = """Note 7 — Property, Plant and Equipment
There were no disposals of property, plant and equipment during the year.
Net book value at the beginning of the year $5,OOO,000
Depreciation charge for the year $400,000
Net book value at the end of the year $5,600,000
"""
    parsed, routing = _write_p5_bundle(tmp_path, note)
    assert _derive_group_capex(parsed_dir=parsed, routing_dir=routing, scenario_id="P5") == (
        None,
        None,
    )


def test_group_capex_bridge_is_scenario_and_note_number_agnostic(tmp_path: Path) -> None:
    note = """Note 42 — Property, Plant and Equipment
There were no disposals of property, plant and equipment during the year.
Net book value at the beginning of the year $5,000,000
Depreciation charge for the year $400,000
Net book value at the end of the year $5,600,000
"""
    parsed, routing = _write_p5_bundle(tmp_path, note, scenario_id="PRIVATE-X")
    value, diagnostic = _derive_group_capex(
        parsed_dir=parsed,
        routing_dir=routing,
        scenario_id="PRIVATE-X",
    )
    assert value == Decimal("1000000")
    assert diagnostic is not None
    assert diagnostic["scenario_id"] == "PRIVATE-X"
    assert diagnostic["note_heading"].startswith("Note 42")


def test_group_capex_bridge_never_uses_another_scenarios_group_document(tmp_path: Path) -> None:
    note = """Note 9 — Property, Plant and Equipment
There were no disposals of property, plant and equipment during the year.
Net book value at the beginning of the year $5,000,000
Depreciation charge for the year $400,000
Net book value at the end of the year $5,600,000
"""
    parsed, routing = _write_p5_bundle(tmp_path, note, scenario_id="SCENARIO-A")
    assert _derive_group_capex(
        parsed_dir=parsed,
        routing_dir=routing,
        scenario_id="SCENARIO-B",
    ) == (None, None)


def _group_plan(definition_id: str, scenario_id: str):
    selector = _selector(MetricCategory.GROUP_CAPEX).model_copy(update={"group_level": True})
    definition = _definition(
        Sum(of=TransactionSet(selector=selector)),
        definition_id=definition_id,
        selectors=(selector,),
    ).model_copy(update={"scenario_id": scenario_id})
    return plan_definition(definition)


def test_group_capex_plan_selection_requires_one_semantic_unresolved_candidate() -> None:
    first = _group_plan("def-a", "SCENARIO-A")
    second = _group_plan("def-b", "SCENARIO-B")
    assert (
        _unique_group_capex_plan(
            (first, second),
            {(first.scenario_id, first.clause_id)},
        )
        == first
    )
    assert (
        _unique_group_capex_plan(
            (first, second),
            {
                (first.scenario_id, first.clause_id),
                (second.scenario_id, second.clause_id),
            },
        )
        is None
    )


def test_group_capex_plan_selection_requires_group_level_selector() -> None:
    selector = _selector(MetricCategory.GROUP_CAPEX).model_copy(update={"group_level": False})
    definition = _definition(
        Sum(of=TransactionSet(selector=selector)),
        definition_id="borrower-capex",
        selectors=(selector,),
    ).model_copy(update={"scenario_id": "PRIVATE-BORROWER"})
    plan = plan_definition(definition)
    assert (
        _unique_group_capex_plan(
            (plan,),
            {(plan.scenario_id, plan.clause_id)},
        )
        is None
    )
