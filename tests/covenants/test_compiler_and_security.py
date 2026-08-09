"""Compiler, security, determinism, and public-smoke helpers for Stage 5D."""

# ruff: noqa: RUF001

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from halyk_agent.config import Settings
from halyk_agent.domain.authority.models import (
    AuthorityDecision,
    AuthorityDomain,
    AuthorityStatus,
)
from halyk_agent.domain.covenants.compiler import compile_covenant_cell
from halyk_agent.domain.covenants.engine import run_covenant_compile
from halyk_agent.domain.covenants.models import Comparator, CompileStatus
from halyk_agent.domain.routing.models import RoutingManifest
from tests.authority.helpers import make_document


def _decision(scenario_id: str, document_id: str) -> AuthorityDecision:
    return AuthorityDecision(
        decision_id=f"d-{scenario_id}",
        scenario_id=scenario_id,
        domain=AuthorityDomain.COVENANT_TERMS,
        status=AuthorityStatus.AUTHORITATIVE,
        rule_id="RULE_TEST",
        reason="test",
        winning_document_ids=(document_id,),
        rejected_document_ids=(),
        candidate_document_ids=(document_id,),
    )


CLAUSE_CAPEX_RATIO = (
    "Пункт 6.1 Maximum Capital Intensity Ratio. Заёмщик, Test Borrower JSC, обязуется не "
    "допускать, чтобы коэффициент капиталоёмкости за период с 2025-01-01 по 2025-12-31 "
    "превышал 0.42x. Коэффициент капиталоёмкости означает отношение совокупных капитальных "
    "затрат за период к сумме операционных расходов и арендных платежей за тот же период."
)

CLAUSE_MIN_REVENUE = (
    "Пункт 6.2 Минимальная выручка по категории. Test Borrower JSC обязуется поддерживать "
    "совокупный объём поступлений по статье «Выручка» за период с 2025-01-01 по 2025-12-31 "
    "на уровне не менее $7,100,000.00."
)

CLAUSE_RP = (
    "Пункт 6.3 Максимальные платежи связанным сторонам. Заёмщик обязуется не допускать, "
    "чтобы совокупный объём платежей в пользу связанных сторон за период с 2025-01-01 по "
    "2025-12-31 превышал $450,000.00."
)


def test_compile_synthetic_three_cells() -> None:
    text = "\n".join([CLAUSE_CAPEX_RATIO, CLAUSE_MIN_REVENUE, CLAUSE_RP])
    doc = make_document(raw_text=text, artifact="loan", sha="a" * 64)
    for clause_id, family in (
        ("6.1", "CAPITAL_INTENSITY_RATIO"),
        ("6.2", "MIN_REVENUE"),
        ("6.3", "MAX_RELATED_PARTY_PAYMENTS"),
    ):
        definition, failure, spans = compile_covenant_cell(
            scenario_id="SX",
            clause_id=clause_id,
            document=doc,
        )
        assert failure is None, failure
        assert definition is not None
        assert definition.family_id == family
        assert definition.evidence.clause_span_ids
        assert spans
        assert definition.rendered


def test_different_thresholds_same_grammar() -> None:
    a = CLAUSE_CAPEX_RATIO.replace("0.42x", "0.55x")
    b = CLAUSE_CAPEX_RATIO.replace("0.42x", "0.10x").replace("Test Borrower JSC", "Other JSC")
    da, fa, _ = compile_covenant_cell(
        scenario_id="A", clause_id="6.1", document=make_document(raw_text=a, sha="1" * 64)
    )
    db, fb, _ = compile_covenant_cell(
        scenario_id="B", clause_id="6.1", document=make_document(raw_text=b, sha="2" * 64)
    )
    assert fa is None and fb is None
    assert da is not None and db is not None
    assert da.family_id == db.family_id
    assert da.comparator is Comparator.LTE
    assert db.comparator is Comparator.LTE
    assert da.threshold.value != db.threshold.value


def test_engine_compiles_template_cells() -> None:
    text = "\n".join([CLAUSE_CAPEX_RATIO, CLAUSE_MIN_REVENUE, CLAUSE_RP])
    doc = make_document(raw_text=text, sha="b" * 64)
    report = run_covenant_compile(
        template_answers={"SX": {"6.1": {}, "6.2": {}, "6.3": {}}},
        decisions=(_decision("SX", doc.document_id),),
        documents=(doc,),
        authority_manifest_hash="c" * 64,
    )
    assert report.manifest.cell_count == 3
    assert report.manifest.definition_count == 3
    assert report.manifest.failure_count == 0


def test_determinism_order_invariant() -> None:
    text = "\n".join([CLAUSE_CAPEX_RATIO, CLAUSE_MIN_REVENUE, CLAUSE_RP])
    doc = make_document(raw_text=text, sha="d" * 64)
    kwargs = dict(
        template_answers={"SX": {"6.1": {}, "6.2": {}, "6.3": {}}},
        decisions=(_decision("SX", doc.document_id),),
        authority_manifest_hash="e" * 64,
    )
    r1 = run_covenant_compile(documents=(doc,), **kwargs)
    r2 = run_covenant_compile(documents=(doc,), **kwargs)
    assert r1.manifest.model_dump_json() == r2.manifest.model_dump_json()
    assert [d.definition_id for d in r1.definitions] == [d.definition_id for d in r2.definitions]


def test_unsupported_formula_does_not_crash_other_cells() -> None:
    odd = (
        "Пункт 6.1 Mystery Metric. The borrower shall maintain quantum flux below 3 bananas "
        "for period from 2025-01-01 to 2025-12-31."
    )
    text = "\n".join([odd, CLAUSE_MIN_REVENUE, CLAUSE_RP])
    doc = make_document(raw_text=text, sha="f" * 64)
    report = run_covenant_compile(
        template_answers={"SX": {"6.1": {}, "6.2": {}, "6.3": {}}},
        decisions=(_decision("SX", doc.document_id),),
        documents=(doc,),
        authority_manifest_hash="1" * 64,
    )
    assert report.manifest.definition_count == 2
    assert any(f.status is CompileStatus.UNSUPPORTED_FORMULA for f in report.failures)


def test_no_scenario_threshold_hardcoding_in_production_sources() -> None:
    root = Path("src/halyk_agent/domain/covenants")
    forbidden_patterns = [
        "P1",
        "P2",
        "P3",
        "P4",
        "P5",
        "P6",
        "P7",
        "P8",
        "P9",
        "P10",
        "B1",
        "B4",
    ]
    # Allow scenario IDs only inside comments? Prefer none at all in production package.
    for path in sorted(root.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value
                for token in forbidden_patterns:
                    if (
                        value == token
                        or value.startswith(f"{token}/")
                        or value.startswith(f"{token}:")
                    ):
                        pytest.fail(f"scenario-specific constant {value!r} in {path}")
            if isinstance(node, ast.Dict):
                for key in node.keys:
                    if (
                        isinstance(key, ast.Constant)
                        and isinstance(key.value, str)
                        and key.value in forbidden_patterns
                    ):
                        pytest.fail(f"scenario-keyed mapping in {path}: {key.value}")


def test_gt_filename_guard(tmp_path: Path) -> None:
    from halyk_agent.app.covenant import CovenantServiceError, assert_no_gt_access

    with pytest.raises(CovenantServiceError):
        assert_no_gt_access(tmp_path / "ground_truth.json")


@pytest.mark.skipif(
    not Path("work/smoke5c1/authority/authority_decisions.jsonl").is_file(),
    reason="public Stage 5C authority smoke artifacts not present",
)
def test_public_authority_compile_smoke() -> None:
    from halyk_agent.adapters.covenants.io import (
        load_authority_decisions,
        load_authority_manifest_hash,
    )
    from halyk_agent.adapters.routing.io import load_template_answers
    from halyk_agent.app.ocr import load_parsed_documents

    authority = Path("work/smoke5c1/authority")
    parsed = Path("work/smoke541/ocr-enriched")
    template = Path("agentic-bank-public/submission_template.json")
    if not parsed.is_dir() or not template.is_file():
        pytest.skip("public parsed/template inputs missing")
    answers = load_template_answers(template)
    decisions = load_authority_decisions(authority / "authority_decisions.jsonl")
    _, documents = load_parsed_documents(parsed)
    report = run_covenant_compile(
        template_answers=answers,
        decisions=decisions,
        documents=tuple(documents),
        authority_manifest_hash=load_authority_manifest_hash(authority / "authority_manifest.json"),
    )
    assert report.manifest.scenario_count == 12
    assert report.manifest.cell_count == 36
    assert report.manifest.definition_count == 36
    assert report.manifest.failure_count == 0
    # unused import guard
    _ = RoutingManifest


def test_unseen_formula_uses_bounded_semantic_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    from halyk_agent.config import Settings
    from halyk_agent.domain.covenants.ast import MetricCategory, Subtract, money_sum
    from halyk_agent.domain.covenants.formulas import FormulaMatch, match_formula
    from halyk_agent.domain.covenants.semantic_formula import SemanticFormulaResult

    clause = (
        "Пункт 6.1 Operating contribution floor. Заёмщик обязуется поддерживать операционный "
        "вклад за период с 2025-01-01 по 2025-12-31 на уровне не менее $100.00. "
        "Операционный вклад определяется отдельной договорной формулой."
    )
    assert match_formula(clause) is None
    doc = make_document(raw_text=clause, sha="9" * 64)
    proposed = FormulaMatch(
        family_id="DEEPSEEK_TYPED_AST_V1",
        metric=Subtract(
            left=money_sum(MetricCategory.REVENUE),
            right=money_sum(MetricCategory.OPEX),
        ),
    )

    def fake_propose(*args: object, **kwargs: object) -> SemanticFormulaResult:
        _ = args, kwargs
        return SemanticFormulaResult(
            formula=proposed,
            diagnostic={"status": "ACCEPTED"},
            model_called=True,
        )

    monkeypatch.setattr("halyk_agent.domain.covenants.compiler.propose_formula", fake_propose)
    definition, failure, spans = compile_covenant_cell(
        scenario_id="SX",
        clause_id="6.1",
        document=doc,
        settings=Settings(semantic_fallback_enabled=True),
    )
    assert failure is None
    assert definition is not None
    assert definition.family_id == "DEEPSEEK_TYPED_AST_V1"
    assert definition.metric_quantity_type.value == "MONEY"
    assert {selector.category for selector in definition.selectors} == {
        MetricCategory.REVENUE,
        MetricCategory.OPEX,
    }
    assert spans


def test_semantic_formula_fallback_remains_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_propose(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        from halyk_agent.domain.covenants.semantic_formula import SemanticFormulaResult

        return SemanticFormulaResult(
            formula=None,
            diagnostic={"reason": "DISABLED"},
            model_called=False,
        )

    def fake_plan(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        from halyk_agent.domain.covenants.semantic_plan import SemanticPlanResult

        return SemanticPlanResult(plan=None, diagnostic={"reason": "DISABLED"}, model_called=False)

    monkeypatch.setattr("halyk_agent.domain.covenants.compiler.propose_formula", fake_propose)
    monkeypatch.setattr("halyk_agent.domain.covenants.compiler.propose_plan", fake_plan)
    odd = (
        "Пункт 6.1 Mystery Metric. The borrower shall maintain quantum flux below 3 bananas "
        "for period from 2025-01-01 to 2025-12-31."
    )
    doc = make_document(raw_text=odd, sha="8" * 64)
    # Assert the contract explicitly rather than relying on the ambient
    # environment: a disabled fallback must reach no semantic entry point.
    definition, failure, _ = compile_covenant_cell(
        scenario_id="SX",
        clause_id="6.1",
        document=doc,
        settings=Settings(semantic_fallback_enabled=False),
    )
    assert calls == 0
    assert definition is None
    assert failure is not None
    assert failure.status is CompileStatus.UNSUPPORTED_FORMULA
