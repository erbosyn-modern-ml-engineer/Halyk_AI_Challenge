"""Apply Stage 10.4 fallback generalization and evidence wiring.

Temporary branch-only helper. Delete before merge.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def write(rel: str, text: str) -> None:
    path = ROOT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def replace_once(rel: str, old: str, new: str) -> None:
    text = read(rel)
    if text.count(old) != 1:
        raise RuntimeError(f"{rel}: expected one exact replacement for {old[:80]!r}")
    write(rel, text.replace(old, new, 1))


def replace_between(rel: str, start: str, end: str, replacement: str) -> None:
    text = read(rel)
    i = text.find(start)
    if i < 0:
        raise RuntimeError(f"{rel}: start marker missing: {start!r}")
    j = text.find(end, i + len(start))
    if j < 0:
        raise RuntimeError(f"{rel}: end marker missing: {end!r}")
    write(rel, text[:i] + replacement + text[j:])


fallback_rel = "src/halyk_agent/solver/fallbacks.py"

replace_between(
    fallback_rel,
    "def _extract_note_section(full_text: str, number: int) -> str | None:\n",
    "def _money_after_label(note: str, match: re.Match[str]) -> tuple[Decimal, str] | None:\n",
    '''def _note_sections(full_text: str) -> tuple[str, ...]:
    """Split source text into numbered note sections without assuming a note number."""

    headings = list(_NOTE_HEADING_RE.finditer(full_text))
    sections: list[str] = []
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(full_text)
        sections.append(full_text[heading.start() : end])
    return tuple(sections)


''',
)

replace_between(
    fallback_rel,
    "def _p5_group_documents(routing_dir: Path) -> tuple[str, ...]:\n",
    "def _derive_p5_group_capex(\n",
    '''def _group_documents_for_scenario(routing_dir: Path, scenario_id: str) -> tuple[str, ...]:
    path = routing_dir / "document_links.jsonl"
    if not path.is_file():
        return ()
    ids: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        scenarios = item.get("scenario_ids")
        if (
            isinstance(scenarios, list)
            and scenario_id in scenarios
            and item.get("group_document") is True
            and isinstance(item.get("document_id"), str)
        ):
            ids.append(item["document_id"])
    return tuple(sorted(set(ids)))


''',
)

replace_between(
    fallback_rel,
    "def _derive_p5_group_capex(\n",
    "def _convert_eur_inputs(\n",
    '''def _derive_group_capex(
    *,
    parsed_dir: Path,
    routing_dir: Path,
    scenario_id: str,
) -> tuple[Decimal | None, dict[str, Any] | None]:
    """Conservative PPE residual bridge selected by covenant and routing semantics.

    The bridge does not assume a public scenario ID or a fixed note number. It
    scans group documents linked to the selected scenario and requires exactly one
    note section with a complete opening/depreciation/closing roll-forward and no
    named competing movement family.
    """

    docs = _parsed_documents(parsed_dir)
    group_ids = _group_documents_for_scenario(routing_dir, scenario_id)
    candidates: list[tuple[Decimal, dict[str, Any]]] = []
    for document_id in group_ids:
        item = docs.get(document_id)
        if item is None:
            continue
        source_file, full_text = item
        for note in _note_sections(full_text):
            opening_matches = list(_OPENING_RE.finditer(note))
            dep_matches = list(_DEPRECIATION_RE.finditer(note))
            closing_matches = list(_CLOSING_RE.finditer(note))
            if not (
                len(opening_matches) == len(dep_matches) == len(closing_matches) == 1
                and _NO_DISPOSALS_RE.search(note)
            ):
                continue

            scrubbed = _NO_DISPOSALS_RE.sub("", note)
            scrubbed = _NO_OTHER_MOVEMENTS_RE.sub("", scrubbed)
            if _OTHER_MOVEMENT_RE.search(scrubbed):
                continue

            opening = _money_after_label(note, opening_matches[0])
            depreciation = _money_after_label(note, dep_matches[0])
            closing = _money_after_label(note, closing_matches[0])
            if opening is None or depreciation is None or closing is None:
                continue
            currencies = {opening[1], depreciation[1], closing[1]}
            if currencies != {"USD"}:
                continue
            residual = closing[0] - opening[0] + depreciation[0]
            if residual <= 0:
                continue
            heading = note.splitlines()[0].strip() if note.splitlines() else ""
            candidates.append(
                (
                    residual,
                    {
                        "strategy": "PPE_ROLL_FORWARD_RESIDUAL_BRIDGE",
                        "scenario_id": scenario_id,
                        "source_file": source_file,
                        "document_id": document_id,
                        "note_heading": heading,
                        "opening_nbv": str(opening[0]),
                        "depreciation": str(depreciation[0]),
                        "closing_nbv": str(closing[0]),
                        "derived_group_capex": str(residual),
                        "assumption": (
                            "competition-only residual: movement families not named in the "
                            "complete PPE roll-forward note section are treated as zero; "
                            "strict Stage 5E remains unresolved"
                        ),
                    },
                )
            )
    unique = {value for value, _diagnostic in candidates}
    if len(unique) != 1 or len(candidates) != 1:
        return None, None
    return candidates[0]


''',
)

replace_between(
    fallback_rel,
    "def _p5_group_input(\n",
    "def _enable_p5_group_selector(\n",
    '''def _group_capex_input(
    amount: Decimal,
    *,
    plan: EvaluationPlan,
    source_file: str,
) -> CalculationInput:
    semantics, sign_rule = sign_contract_for_category(MetricCategory.GROUP_CAPEX)
    start = plan.period.start_date or date(2025, 1, 1)
    end = plan.period.end_date or date(2025, 12, 31)
    return CalculationInput(
        input_id=deterministic_id(
            "stage8-fallback", plan.scenario_id, "GROUP_CAPEX", str(amount)
        ),
        scenario_id=plan.scenario_id,
        source_kind=InputSourceKind.AUTHORITATIVE_FACT,
        derived_input_id="stage8-ppe-roll-forward-residual",
        category=MetricCategory.GROUP_CAPEX,
        selector_categories=(MetricCategory.GROUP_CAPEX,),
        membership_reasons=("STAGE8_PPE_ROLL_FORWARD_RESIDUAL",),
        amount=amount,
        source_amount=amount,
        metric_amount=amount,
        amount_semantics=semantics,
        sign_rule=sign_rule,
        currency="USD",
        period_semantics=InputPeriodSemantics.FLOW,
        period_start=start,
        period_end=end,
        related_party=RelatedPartyStatus.FALSE,
        entity_scope=EntityScopeKind.GROUP,
        flags=("GROUP_LEVEL_SOURCE", "STAGE8_COMPETITIVE_FALLBACK"),
        provenance_refs=(f"stage8:ppe_roll_forward:{source_file}",),
        classification_rule="STAGE8_PPE_ROLL_FORWARD_RESIDUAL",
    )


''',
)

replace_once(
    fallback_rel,
    "def _enable_p5_group_selector(\n",
    "def _enable_group_capex_selector(\n",
)

replace_once(
    fallback_rel,
    "def build_competitive_fallbacks(\n",
    '''def _unique_group_capex_plan(
    plans: tuple[EvaluationPlan, ...],
    target_keys: set[tuple[str, str]],
) -> EvaluationPlan | None:
    """Return the sole unresolved plan that semantically requests GROUP_CAPEX."""

    candidates: list[EvaluationPlan] = []
    for plan in plans:
        key = (plan.scenario_id, plan.clause_id)
        if key not in target_keys:
            continue
        if any(
            node.selector is not None and node.selector.category is MetricCategory.GROUP_CAPEX
            for node in plan.nodes
        ):
            candidates.append(plan)
    if len(candidates) != 1:
        return None
    return candidates[0]


def build_competitive_fallbacks(
''',
)

old_build = '''    coverage = context.selector_coverage
    readiness = context.definition_readiness
    p5_plan = next(
        (plan for plan in evaluation.plans if (plan.scenario_id, plan.clause_id) == ("P5", "6.1")),
        None,
    )
    p5_amount, p5_diagnostic = _derive_p5_group_capex(
        parsed_dir=parsed_dir,
        routing_dir=routing_dir,
    )
    if p5_plan is not None and p5_amount is not None and p5_diagnostic is not None:
        working_inputs = tuple(
            (
                *working_inputs,
                _p5_group_input(p5_amount, plan=p5_plan, source_file=p5_diagnostic["source_file"]),
            )
        )
        coverage, readiness = _enable_p5_group_selector(
            coverage,
            readiness,
            definition_id=p5_plan.definition_id,
        )
        diagnostics.append(p5_diagnostic)
'''
new_build = '''    coverage = context.selector_coverage
    readiness = context.definition_readiness
    group_capex_plan = _unique_group_capex_plan(evaluation.plans, target_keys)
    if group_capex_plan is not None:
        group_capex_amount, group_capex_diagnostic = _derive_group_capex(
            parsed_dir=parsed_dir,
            routing_dir=routing_dir,
            scenario_id=group_capex_plan.scenario_id,
        )
        if group_capex_amount is not None and group_capex_diagnostic is not None:
            working_inputs = tuple(
                (
                    *working_inputs,
                    _group_capex_input(
                        group_capex_amount,
                        plan=group_capex_plan,
                        source_file=group_capex_diagnostic["source_file"],
                    ),
                )
            )
            coverage, readiness = _enable_group_capex_selector(
                coverage,
                readiness,
                definition_id=group_capex_plan.definition_id,
            )
            diagnostics.append(group_capex_diagnostic)
'''
replace_once(fallback_rel, old_build, new_build)

# M5: fallback results use the fallback context for causal evidence replay.
final_rel = "src/halyk_agent/solver/submission/final.py"
replace_once(
    final_rel,
    "    fallback_results: dict[tuple[str, str], CovenantEvaluationResult] | None = None,\n"
    "    compile_failures: tuple[CovenantCompileFailure, ...] = (),\n",
    "    fallback_results: dict[tuple[str, str], CovenantEvaluationResult] | None = None,\n"
    "    fallback_context: EvaluationContext | None = None,\n"
    "    compile_failures: tuple[CovenantCompileFailure, ...] = (),\n",
)
replace_once(
    final_rel,
    '''            evidence = None
            if not used_fallback:
                evidence = select_causal_evidence(
                    plan=plan,
                    result=result,
                    context=context,
                    adjustments=adjustments,
                    classified=classified,
                )
''',
    '''            evidence_context = (
                fallback_context
                if used_fallback and fallback_context is not None
                else context
            )
            evidence = select_causal_evidence(
                plan=plan,
                result=result,
                context=evidence_context,
                adjustments=adjustments,
                classified=classified,
            )
''',
)

solve_rel = "src/halyk_agent/solver/solve.py"
replace_once(
    solve_rel,
    "            fallback_results=fallback.results,\n"
    "            compile_failures=pipeline.covenants.failures,\n",
    "            fallback_results=fallback.results,\n"
    "            fallback_context=fallback.context,\n"
    "            compile_failures=pipeline.covenants.failures,\n",
)

# Update existing fallback tests to call the semantic helper and add M8 probes.
test_rel = "tests/solver/test_competitive_fallbacks.py"
test_text = read(test_rel)
test_text = test_text.replace(
    "from halyk_agent.solver.fallbacks import _derive_p5_group_capex, _settlement_eur_usd_rate",
    "from halyk_agent.solver.fallbacks import (\n"
    "    _derive_group_capex,\n"
    "    _settlement_eur_usd_rate,\n"
    "    _unique_group_capex_plan,\n"
    ")",
)
test_text = test_text.replace(
    "def _write_p5_bundle(root: Path, note: str) -> tuple[Path, Path]:",
    "def _write_p5_bundle(\n"
    "    root: Path, note: str, *, scenario_id: str = \"P5\"\n"
    ") -> tuple[Path, Path]:",
)
test_text = test_text.replace('"scenario_ids": ["P5"],', '"scenario_ids": [scenario_id],')
test_text = test_text.replace(
    "_derive_p5_group_capex(parsed_dir=parsed, routing_dir=routing)",
    '_derive_group_capex(parsed_dir=parsed, routing_dir=routing, scenario_id="P5")',
)
test_text = test_text.replace(
    "from halyk_agent.domain.transaction_taxonomy.models import AdjustmentEvent, AdjustmentEventType\n",
    "from halyk_agent.domain.covenant_evaluation import plan_definition\n"
    "from halyk_agent.domain.covenants.ast import MetricCategory, Sum, TransactionSet\n"
    "from halyk_agent.domain.transaction_taxonomy.models import AdjustmentEvent, AdjustmentEventType\n"
    "from tests.covenant_evaluation._helpers import _definition, _selector\n",
)
test_text += '''


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
    assert _unique_group_capex_plan(
        (first, second),
        {(first.scenario_id, first.clause_id)},
    ) == first
    assert _unique_group_capex_plan(
        (first, second),
        {
            (first.scenario_id, first.clause_id),
            (second.scenario_id, second.clause_id),
        },
    ) is None
'''
write(test_rel, test_text)

# M5 wiring regression: fallback evidence must replay against fallback context.
write(
    "tests/solver/test_fallback_evidence_wiring.py",
    '''"""Fallback submission cells use fallback context for causal evidence replay."""

from __future__ import annotations

from decimal import Decimal

from halyk_agent.domain.covenant_evaluation import (
    EvaluationExecutor,
    EvaluationManifest,
    EvaluationReport,
    EvaluationStatus,
    plan_definition,
)
from halyk_agent.domain.covenants.ast import MetricCategory, Sum, TransactionSet
from halyk_agent.domain.covenants.quantity import QuantityType, TypedQuantity
from halyk_agent.solver.submission.final import build_final_submission
from tests.covenant_evaluation._helpers import _context, _definition, _input, _selector


def test_fallback_result_uses_fallback_context_for_evidence(monkeypatch) -> None:
    selector = _selector(MetricCategory.REVENUE)
    definition = _definition(
        Sum(of=TransactionSet(selector=selector)),
        selectors=(selector,),
        threshold=TypedQuantity(
            quantity_type=QuantityType.MONEY,
            value=Decimal("100"),
            currency="USD",
        ),
    )
    strict_context = _context(definition, (_input("strict", "50"),))
    fallback_context = _context(definition, (_input("fallback", "125"),))
    plan = plan_definition(definition)
    strict_resolved = EvaluationExecutor().execute(plan, strict_context)
    fallback_result = EvaluationExecutor().execute(plan, fallback_context)
    strict_result = strict_resolved.model_copy(
        update={
            "status": EvaluationStatus.UNRESOLVED,
            "compliance_status": None,
            "actual": None,
        }
    )
    manifest = EvaluationManifest(
        covenant_manifest_hash="c",
        taxonomy_manifest_hash="t",
        calculation_inputs_hash="i",
        selector_coverage_hash="s",
        definition_readiness_hash="d",
        plan_count=1,
        result_count=1,
        resolved_count=0,
        unresolved_count=1,
        error_count=0,
        not_activated_count=0,
        compliant_count=0,
        breach_count=0,
        plans_hash="p",
        results_hash="r",
    )
    report = EvaluationReport(manifest=manifest, plans=(plan,), results=(strict_result,))
    seen: list[object] = []

    def fake_evidence(**kwargs):
        seen.append(kwargs["context"])
        return "TX-fallback"

    monkeypatch.setattr(
        "halyk_agent.solver.submission.final.select_causal_evidence",
        fake_evidence,
    )
    template = {
        "team": "team",
        "contact_email": "team@example.com",
        "model": "model",
        "answers": {"S1": {"6.1": {"status": None, "actual": None, "evidence_txn_id": None}}},
    }
    document, unresolved = build_final_submission(
        template,
        evaluation=report,
        context=strict_context,
        fallback_results={(plan.scenario_id, plan.clause_id): fallback_result},
        fallback_context=fallback_context,
        adjustments=(),
        classified=(),
    )
    assert unresolved == ()
    assert document.answers["S1"]["6.1"].evidence_txn_id == "TX-fallback"
    assert seen == [fallback_context]
''',
)

print("fallback generalization patch applied")
