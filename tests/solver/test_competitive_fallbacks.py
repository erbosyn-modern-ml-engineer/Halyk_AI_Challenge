"""Adversarial contracts for bounded Stage 8 competition fallbacks."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from halyk_agent.domain.transaction_taxonomy.models import AdjustmentEvent, AdjustmentEventType
from halyk_agent.solver.fallbacks import _derive_p5_group_capex, _settlement_eur_usd_rate


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


def _write_p5_bundle(root: Path, note: str) -> tuple[Path, Path]:
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
                "scenario_ids": ["P5"],
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
    value, diagnostic = _derive_p5_group_capex(parsed_dir=parsed, routing_dir=routing)
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
    value, diagnostic = _derive_p5_group_capex(parsed_dir=parsed, routing_dir=routing)
    assert value is None
    assert diagnostic is None
