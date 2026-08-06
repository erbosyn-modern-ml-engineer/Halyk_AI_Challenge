"""Content-shape detection for answer-key / ground-truth payloads."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from halyk_agent.solver.errors import AnswerKeyAccessBlockedError
from halyk_agent.solver.mode import SolverMode, get_solver_mode


def _has_cell_shape(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False
    keys = set(obj)
    return {"status", "actual", "evidence_txn_id"}.issubset(keys)


def _covenant_map_shape(obj: Any) -> bool:
    if not isinstance(obj, dict) or not obj:
        return False
    samples = list(obj.values())[:5]
    return all(_has_cell_shape(item) for item in samples)


def is_answer_key_payload(obj: Any) -> bool:
    """True when JSON looks like scored ground truth / answer key."""
    if not isinstance(obj, dict):
        return False
    scenarios = obj.get("scenarios")
    if isinstance(scenarios, dict) and scenarios:
        first = next(iter(scenarios.values()))
        if isinstance(first, dict) and _covenant_map_shape(first.get("covenants")):
            return True
    answers = obj.get("answers")
    if isinstance(answers, dict) and answers:
        first = next(iter(answers.values()))
        if _covenant_map_shape(first):
            # submission templates also have this shape but with null cells and team metadata
            # Treat as answer-key only when at least one cell has non-null actual/status
            for scenario in answers.values():
                if not isinstance(scenario, dict):
                    continue
                for cell in scenario.values():
                    if isinstance(cell, dict) and (
                        cell.get("status") is not None or cell.get("actual") is not None
                    ):
                        return True
    return False


def peek_json_is_answer_key(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return is_answer_key_payload(payload)


def block_answer_key_read(path: Path) -> None:
    """Competition mode must not open answer-key shaped files."""
    if get_solver_mode() is SolverMode.COMPETITION and peek_json_is_answer_key(path):
        raise AnswerKeyAccessBlockedError(
            f"answer-key shaped file blocked in competition mode: {path.name}"
        )
