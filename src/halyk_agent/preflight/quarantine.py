"""Answer-key content-shape detection (preflight-only; solver must not import)."""

from __future__ import annotations

from typing import Any


def _has_cell_shape(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False
    return {"status", "actual", "evidence_txn_id"}.issubset(obj)


def _covenant_map_shape(obj: Any) -> bool:
    if not isinstance(obj, dict) or not obj:
        return False
    samples = list(obj.values())[:5]
    return all(_has_cell_shape(item) for item in samples)


def is_answer_key_payload(obj: Any) -> bool:
    """True when JSON looks like scored ground truth / answer key (shape only)."""
    if not isinstance(obj, dict):
        return False
    scenarios = obj.get("scenarios")
    if isinstance(scenarios, dict) and scenarios:
        first = next(iter(scenarios.values()))
        if isinstance(first, dict) and _covenant_map_shape(first.get("covenants")):
            return True
    answers = obj.get("answers")
    if isinstance(answers, dict) and answers and _covenant_map_shape(next(iter(answers.values()))):
        for scenario in answers.values():
            if not isinstance(scenario, dict):
                continue
            for cell in scenario.values():
                if isinstance(cell, dict) and (
                    cell.get("status") is not None
                    or cell.get("actual") is not None
                    or cell.get("evidence_txn_id") is not None
                ):
                    return True
    return False
