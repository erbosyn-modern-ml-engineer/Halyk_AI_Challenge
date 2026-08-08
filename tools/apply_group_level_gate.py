"""Temporary Stage 10.4 narrowing patch. Delete before merge."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

fallback = ROOT / "src/halyk_agent/solver/fallbacks.py"
text = fallback.read_text(encoding="utf-8")
old = '''        if any(
            node.selector is not None and node.selector.category is MetricCategory.GROUP_CAPEX
            for node in plan.nodes
        ):
            candidates.append(plan)
'''
new = '''        if any(
            node.selector is not None
            and node.selector.category is MetricCategory.GROUP_CAPEX
            and node.selector.group_level is True
            for node in plan.nodes
        ):
            candidates.append(plan)
'''
if text.count(old) != 1:
    raise RuntimeError("unique GROUP_CAPEX plan selector block not found exactly once")
fallback.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")

test = ROOT / "tests/solver/test_competitive_fallbacks.py"
test_text = test.read_text(encoding="utf-8")
append = '''


def test_group_capex_plan_selection_requires_group_level_selector() -> None:
    selector = _selector(MetricCategory.GROUP_CAPEX).model_copy(update={"group_level": False})
    definition = _definition(
        Sum(of=TransactionSet(selector=selector)),
        definition_id="borrower-capex",
        selectors=(selector,),
    ).model_copy(update={"scenario_id": "PRIVATE-BORROWER"})
    plan = plan_definition(definition)
    assert _unique_group_capex_plan(
        (plan,),
        {(plan.scenario_id, plan.clause_id)},
    ) is None
'''
if "test_group_capex_plan_selection_requires_group_level_selector" in test_text:
    raise RuntimeError("group-level regression already present")
test.write_text(test_text + append, encoding="utf-8", newline="\n")

print("group-level fallback gate applied")
