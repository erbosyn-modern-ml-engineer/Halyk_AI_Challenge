"""Acceptance-fix regressions for Stage 5D Opus findings."""

# ruff: noqa: RUF001

from __future__ import annotations

from decimal import Decimal

import pytest

from halyk_agent.domain.authority.models import (
    AuthorityDecision,
    AuthorityDomain,
    AuthorityStatus,
)
from halyk_agent.domain.covenants.compiler import compile_covenant_cell
from halyk_agent.domain.covenants.engine import run_covenant_compile
from halyk_agent.domain.covenants.locate import find_in_clause, locate_clause
from halyk_agent.domain.covenants.models import (
    CompileStatus,
    CovenantModifierKind,
    PeriodKind,
    ScopeKind,
    ScopeProvenance,
)
from halyk_agent.domain.covenants.modifiers import (
    extract_modifier_matches,
    extract_modifiers,
)
from halyk_agent.domain.covenants.parse import (
    parse_threshold,
    related_party_phrase,
    resolve_scope,
)
from halyk_agent.domain.covenants.render import render_covenant_definition, render_expr
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


def test_related_expenses_not_related_party_scope() -> None:
    text = (
        "Пункт 6.2 Efficiency Cover. Заёмщик обязуется поддерживать отношение выручки "
        "к сумме оплату труда и коммунальных платежей и связанных с ними расходов "
        "за период с 2025-01-01 по 2025-12-31 на уровне не менее 1.20x."
    )
    assert related_party_phrase(text) is None
    doc = make_document(raw_text=text, sha="a" * 64)
    definition, failure, _ = compile_covenant_cell(scenario_id="SX", clause_id="6.2", document=doc)
    assert failure is None, failure
    assert definition is not None
    assert definition.scope.scope_kind is ScopeKind.BORROWER
    assert definition.scope.provenance is ScopeProvenance.DEFAULT_BORROWER_BY_RULE


def test_related_party_positive_control() -> None:
    text = (
        "Пункт 6.3 Related. Заёмщик обязуется не допускать, чтобы платежи связанным "
        "сторонам за период с 2025-01-01 по 2025-12-31 превышали $450,000.00."
    )
    assert related_party_phrase(text)
    doc = make_document(raw_text=text, sha="b" * 64)
    definition, failure, _ = compile_covenant_cell(scenario_id="SX", clause_id="6.3", document=doc)
    assert failure is None, failure
    assert definition is not None
    assert definition.scope.scope_kind is ScopeKind.RELATED_PARTY_SET


def test_english_related_expenses_negative_and_payments_positive() -> None:
    assert related_party_phrase("related expenses must not exceed") is None
    assert related_party_phrase("payments to related parties must not exceed")


def test_comparator_evidence_stays_inside_own_clause() -> None:
    earlier = (
        "Пункт 6.1 Distractor. The borrower must maintain quantum below 1 bananas "
        "for period from 2025-01-01 to 2025-12-31. The word не appears early."
    )
    target = (
        "Пункт 6.2 Минимальная выручка. Заёмщик обязуется поддерживать совокупный "
        "объём поступлений по статье Выручка за период с 2025-01-01 по 2025-12-31 "
        "на уровне не менее $7,100,000.00."
    )
    doc = make_document(raw_text=earlier + "\n" + target, sha="c" * 64)
    definition, failure, spans = compile_covenant_cell(
        scenario_id="SX", clause_id="6.2", document=doc
    )
    assert failure is None, failure
    assert definition is not None
    assert definition.evidence.comparator_span_ids
    by_id = {s.id: s for s in spans}
    located = locate_clause(doc, clause_id="6.2")
    assert located is not None
    for sid in definition.evidence.comparator_span_ids:
        span = by_id[sid]
        assert span.quote in located.text


def test_formula_evidence_covers_formula_region() -> None:
    text = (
        "Пункт 6.1 Maximum Capital Intensity Ratio. Заёмщик обязуется не допускать, "
        "чтобы коэффициент капиталоёмкости за период с 2025-01-01 по 2025-12-31 "
        "превышал 0.42x. Коэффициент капиталоёмкости означает отношение совокупных "
        "капитальных затрат за период к сумме операционных расходов и арендных платежей."
    )
    doc = make_document(raw_text=text, sha="d" * 64)
    definition, failure, spans = compile_covenant_cell(
        scenario_id="SX", clause_id="6.1", document=doc
    )
    assert failure is None, failure
    assert definition is not None
    assert definition.evidence.formula_span_ids
    by_id = {s.id: s for s in spans}
    joined = "".join(by_id[i].quote for i in definition.evidence.formula_span_ids)
    assert "капиталоёмкости" in joined or "капитальных" in joined
    assert len(joined) > 180


def test_activation_evidence_nonempty() -> None:
    text = (
        "Пункт 6.1 Springing Drawdown Leverage. Ковенант применяется только при условии, "
        "что совокупный объём поступлений по финансированию превышает $4,000,000.00. "
        "При активации отношение поступлений по финансированию к EBITDA за период "
        "с 2025-01-01 по 2025-12-31 не должно превышать 1.70x."
    )
    doc = make_document(raw_text=text, sha="e" * 64)
    definition, failure, _ = compile_covenant_cell(scenario_id="SX", clause_id="6.1", document=doc)
    assert failure is None, failure
    assert definition is not None
    assert definition.activation_condition is not None
    assert definition.evidence.activation_span_ids
    assert definition.activation_condition.evidence_span_ids
    assert definition.activation_condition.threshold.value == Decimal("4000000.00")
    assert definition.threshold.value == Decimal("1.70")


def test_ambiguous_threshold_fails_closed() -> None:
    text = (
        "Пункт 6.1 Maximum CAPEX. Заёмщик обязуется не допускать, чтобы совокупные "
        "капитальные затраты за период с 2025-01-01 по 2025-12-31 превышали $1,000,000.00 "
        "и также обязуется не допускать превышения $2,500,000.00."
    )
    doc = make_document(raw_text=text, sha="f" * 64)
    definition, failure, _ = compile_covenant_cell(scenario_id="SX", clause_id="6.1", document=doc)
    assert definition is None
    assert failure is not None
    assert failure.status is CompileStatus.AMBIGUOUS_THRESHOLD


@pytest.mark.parametrize(
    "token",
    ["$1,00,0.0.0", "$1..00", "1.2.3x", "$"],
)
def test_malformed_numeric_rejected(token: str) -> None:
    result = parse_threshold(f"must not exceed {token} for the period")
    assert result.status == "malformed"


def test_mixed_operand_scopes_expression_scoped() -> None:
    text = (
        "Пункт 6.1 Group CAPEX to Borrower EBITDA. Отношение капитальных затрат Группы "
        "к EBITDA Заёмщика за период с 2025-01-01 по 2025-12-31 не должно превышать 0.35x."
    )
    doc = make_document(raw_text=text, sha="g" * 64)
    definition, failure, _ = compile_covenant_cell(scenario_id="SX", clause_id="6.1", document=doc)
    assert failure is None, failure
    assert definition is not None
    assert definition.scope.scope_kind is ScopeKind.EXPRESSION_SCOPED
    rendered = render_expr(definition.metric)
    assert "GROUP" in rendered
    assert "BORROWER" in rendered
    assert "/" in rendered


def test_mixed_period_renderer_shows_both_components() -> None:
    text = (
        "Пункт 6.1 Personnel obligations. Заёмщик обязуется не допускать, чтобы "
        "обязательства по персоналу, включая оплату труда за период с 2025-01-01 "
        "по 2025-12-31 и обязательства по выходным пособиям по состоянию на 2025-12-31, "
        "превышали $900,000.00."
    )
    doc = make_document(raw_text=text, sha="h" * 64)
    definition, failure, _ = compile_covenant_cell(scenario_id="SX", clause_id="6.1", document=doc)
    assert failure is None, failure
    assert definition is not None
    assert definition.period.period_kind is PeriodKind.MIXED
    rendered = render_covenant_definition(definition)
    assert "2025-01-01" in rendered
    assert "2025-12-31" in rendered
    assert "as-of" in rendered.casefold() or "as of" in rendered.casefold()


@pytest.mark.parametrize(
    ("text", "currency"),
    [
        ("must not exceed $1,000.00", "USD"),
        ("must not exceed USD 1,000.00", "USD"),
        ("must not exceed EUR 1,000.00", "EUR"),
        ("must not exceed €1,000.00", "EUR"),
        ("must not exceed ₸1,000.00", "KZT"),
    ],
)
def test_currency_from_source(text: str, currency: str) -> None:
    result = parse_threshold(text)
    assert result.status == "ok"
    assert result.quantity is not None
    assert result.quantity.currency == currency


def test_modifiers_not_silently_dropped() -> None:
    text = (
        "Пункт 6.1 Adjusted. С учётом порога существенности аудитора $250,000.00, включая "
        "суммы, переквалифицированные в не операционные, отношение скорректированной EBITDA "
        "к выручке за период с 2025-01-01 по 2025-12-31 должно составлять не менее 0.18x."
    )
    mods = extract_modifiers(text)
    kinds = {m.kind for m in mods}
    assert CovenantModifierKind.MATERIALITY_FLOOR in kinds
    floor = next(m for m in mods if m.kind is CovenantModifierKind.MATERIALITY_FLOOR)
    assert floor.threshold is not None
    assert floor.threshold.value == Decimal("250000.00")
    assert floor.threshold.currency == "USD"
    doc = make_document(raw_text=text, sha="i" * 64)
    definition, failure, _ = compile_covenant_cell(scenario_id="SX", clause_id="6.1", document=doc)
    assert failure is None, failure
    assert definition is not None
    assert definition.modifiers
    assert any(m.kind is CovenantModifierKind.MATERIALITY_FLOOR for m in definition.modifiers)


def test_fail_closed_cell_does_not_block_others() -> None:
    bad = (
        "Пункт 6.1 Cap. Заёмщик обязуется не допускать, чтобы капитальные затраты "
        "за период с 2025-01-01 по 2025-12-31 превышали $1,00,0.0.0."
    )
    good = (
        "Пункт 6.2 Минимальная выручка. Заёмщик обязуется поддерживать совокупный объём "
        "поступлений по статье Выручка за период с 2025-01-01 по 2025-12-31 на уровне "
        "не менее $7,100,000.00."
    )
    doc = make_document(raw_text=bad + "\n" + good, sha="j" * 64)
    report = run_covenant_compile(
        template_answers={"SX": {"6.1": {}, "6.2": {}}},
        decisions=(_decision("SX", doc.document_id),),
        documents=(doc,),
        authority_manifest_hash="1" * 64,
    )
    assert report.manifest.definition_count == 1
    assert any(f.status is CompileStatus.MALFORMED_THRESHOLD for f in report.failures)


def test_clause_local_find_rejects_outside_needle() -> None:
    text = (
        "Пункт 6.1 First. The borrower must not exceed anything early.\n"
        "Пункт 6.2 Second. Заёмщик обязуется поддерживать выручку за период "
        "с 2025-01-01 по 2025-12-31 на уровне не менее $1,000,000.00."
    )
    doc = make_document(raw_text=text, sha="k" * 64)
    located = locate_clause(doc, clause_id="6.2")
    assert located is not None
    assert find_in_clause(doc, located, needle="must not exceed") is None
    assert find_in_clause(doc, located, needle="не менее") is not None


def test_scope_default_provenance_without_fake_evidence() -> None:
    scope = resolve_scope(
        "Заёмщик обязуется поддерживать выручку не менее $1",
        selectors=(),
    )
    assert scope.scope_kind is ScopeKind.BORROWER
    assert scope.provenance is ScopeProvenance.DEFAULT_BORROWER_BY_RULE
    assert scope.matched_text is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "определяемых на основании аудированной отчётности Заёмщика с учётом любой "
            "переквалификации затрат в состав Процентных расходов, принятой аудиторами "
            "Заёмщика для целей соблюдения ковенанта.",
            CovenantModifierKind.AUDITOR_RECLASSIFICATION_INCLUDE,
        ),
        (
            "определяемую на основании аудированной отчётности Заёмщика с учётом любой "
            "корректировки по методу начисления или переквалификации периода, принятой "
            "аудиторами Заёмщика для целей соблюдения ковенанта.",
            CovenantModifierKind.AUDITOR_RECLASSIFICATION_INCLUDE,
        ),
        (
            "суммы, отнесённые к данной статье в аудированной финансовой отчётности "
            "Заёмщика с учётом переквалификаций, произведённых аудиторами Заёмщика для "
            "целей соблюдения ковенантов.",
            CovenantModifierKind.AUDITOR_RECLASSIFICATION_INCLUDE,
        ),
        (
            "Суммы, переквалифицированные независимым аудитором Заёмщика в состав "
            "финансовых или иных неоперационных статей, в счёт исполнения настоящего "
            "ковенанта не засчитываются независимо от их первоначального отражения в учёте.",
            CovenantModifierKind.AUDITOR_RECLASSIFICATION_EXCLUDE,
        ),
    ],
)
def test_ru_modifier_families_from_source_phrasing(
    text: str, expected: CovenantModifierKind
) -> None:
    matches = extract_modifier_matches(text)
    kinds = {m.kind for m in matches}
    assert expected in kinds
    if expected is CovenantModifierKind.AUDITOR_RECLASSIFICATION_INCLUDE:
        assert CovenantModifierKind.AUDITOR_RECLASSIFICATION_EXCLUDE not in kinds
    if expected is CovenantModifierKind.AUDITOR_RECLASSIFICATION_EXCLUDE:
        assert CovenantModifierKind.AUDITOR_RECLASSIFICATION_INCLUDE not in kinds
    hit = next(m for m in matches if m.kind is expected)
    assert hit.quotes
    for quote in hit.quotes:
        assert quote in text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Revenue is determined taking into account reclassifications made by the auditor.",
            CovenantModifierKind.AUDITOR_RECLASSIFICATION_INCLUDE,
        ),
        (
            "Amounts reclassified by the auditor as non-operating expenses are reflected.",
            CovenantModifierKind.AUDITOR_RECLASSIFICATION_INCLUDE,
        ),
        (
            "Amounts reclassified as financial items shall not be counted "
            "toward covenant compliance.",
            CovenantModifierKind.AUDITOR_RECLASSIFICATION_EXCLUDE,
        ),
        (
            "Revenue is subject to accrual adjustments approved for covenant measurement.",
            CovenantModifierKind.AUDITOR_RECLASSIFICATION_INCLUDE,
        ),
        (
            "The metric reflects period reclassification approved by the auditor.",
            CovenantModifierKind.AUDITOR_RECLASSIFICATION_INCLUDE,
        ),
    ],
)
def test_en_modifier_parity_positives(text: str, expected: CovenantModifierKind) -> None:
    kinds = {m.kind for m in extract_modifier_matches(text)}
    assert expected in kinds
    if expected is CovenantModifierKind.AUDITOR_RECLASSIFICATION_INCLUDE:
        assert CovenantModifierKind.AUDITOR_RECLASSIFICATION_EXCLUDE not in kinds
    if expected is CovenantModifierKind.AUDITOR_RECLASSIFICATION_EXCLUDE:
        assert CovenantModifierKind.AUDITOR_RECLASSIFICATION_INCLUDE not in kinds


@pytest.mark.parametrize(
    "text",
    [
        "переквалификация сотрудника на другую должность не влияет на штатное расписание",
        "корректировка адреса компании отражена в реестре",
        "period changed for administrative reporting only",
        "auditor discussed the report with management",
        "the company may reclassify departments next year",
        "auditor discussed possible classifications without covenant effect",
        "employee was reclassified to another role",
        "administrative period adjustment for internal calendars",
        "The auditor reclassified the expense.",
        "reclassified by the auditor",
    ],
)
def test_modifier_negative_controls(text: str) -> None:
    assert extract_modifier_matches(text) == ()


def test_modifier_evidence_coupled_to_same_match() -> None:
    text = (
        "Пункт 6.2 Revenue. Для целей настоящей статьи под поступлениями по статье "
        "«Выручка» понимаются суммы в аудированной финансовой отчётности Заёмщика "
        "с учётом переквалификаций, произведённых аудиторами Заёмщика для целей "
        "соблюдения ковенантов. Заёмщик обязуется поддерживать такие поступления "
        "за период с 2025-01-01 по 2025-12-31 на уровне не менее $1,000,000.00."
    )
    matches = extract_modifier_matches(text)
    assert matches
    doc = make_document(raw_text=text, sha="m" * 64)
    definition, failure, spans = compile_covenant_cell(
        scenario_id="SX", clause_id="6.2", document=doc
    )
    assert failure is None, failure
    assert definition is not None
    assert definition.modifiers
    by_id = {s.id: s for s in spans}
    for mod in definition.modifiers:
        assert mod.evidence_span_ids
        for sid in mod.evidence_span_ids:
            span = by_id[sid]
            assert any(
                q in text and (span.quote in q or q in span.quote or span.quote in text)
                for q in matches[0].quotes
            ) or (span.quote in text)


def test_exclude_modifier_handles_distant_cues() -> None:
    text = (
        "Пункт 6.2 Revenue floor. Заёмщик обязуется поддерживать выручку за период "
        "с 2025-01-01 по 2025-12-31 на уровне не менее $6,500,000.00. "
        "Суммы, переквалифицированные независимым аудитором Заёмщика в состав "
        "финансовых или иных неоперационных статей, в счёт исполнения настоящего "
        "ковенанта не засчитываются независимо от их первоначального отражения в учёте."
    )
    matches = extract_modifier_matches(text)
    kinds = {m.kind for m in matches}
    assert CovenantModifierKind.AUDITOR_RECLASSIFICATION_EXCLUDE in kinds
    assert CovenantModifierKind.AUDITOR_RECLASSIFICATION_INCLUDE not in kinds
    hit = next(
        m for m in matches if m.kind is CovenantModifierKind.AUDITOR_RECLASSIFICATION_EXCLUDE
    )
    assert len(hit.quotes) >= 2
    doc = make_document(raw_text=text, sha="n" * 64)
    definition, failure, _ = compile_covenant_cell(scenario_id="SX", clause_id="6.2", document=doc)
    assert failure is None, failure
    assert definition is not None
    mod = next(
        m
        for m in definition.modifiers
        if m.kind is CovenantModifierKind.AUDITOR_RECLASSIFICATION_EXCLUDE
    )
    assert mod.evidence_span_ids
    assert len(mod.evidence_span_ids) >= 1


def test_ru_exclude_only_polarity_no_include() -> None:
    text = (
        "Суммы, переклассифицированные аудиторами Заёмщика из указанной категории, "
        "исключаются из расчёта."
    )
    kinds = {m.kind for m in extract_modifier_matches(text)}
    assert CovenantModifierKind.AUDITOR_RECLASSIFICATION_EXCLUDE in kinds
    assert CovenantModifierKind.AUDITOR_RECLASSIFICATION_INCLUDE not in kinds
    hit = next(
        m
        for m in extract_modifier_matches(text)
        if m.kind is CovenantModifierKind.AUDITOR_RECLASSIFICATION_EXCLUDE
    )
    joined = " ".join(hit.quotes).casefold()
    assert "исключ" in joined or "исключаются" in " ".join(hit.quotes)


def test_en_exclude_only_polarity_no_include() -> None:
    text = "Amounts reclassified by the auditor as non-operating items shall not be counted."
    kinds = {m.kind for m in extract_modifier_matches(text)}
    assert CovenantModifierKind.AUDITOR_RECLASSIFICATION_EXCLUDE in kinds
    assert CovenantModifierKind.AUDITOR_RECLASSIFICATION_INCLUDE not in kinds


def test_en_include_only_polarity_no_exclude() -> None:
    text = "Amounts reclassified by the auditor shall be included in the covenant calculation."
    kinds = {m.kind for m in extract_modifier_matches(text)}
    assert CovenantModifierKind.AUDITOR_RECLASSIFICATION_INCLUDE in kinds
    assert CovenantModifierKind.AUDITOR_RECLASSIFICATION_EXCLUDE not in kinds


def test_ru_include_reflection_polarity_no_exclude() -> None:
    text = (
        "сумма, которую аудиторы признают подлежащей отражению как Капитальные затраты, "
        "учитывается при расчёте ковенанта."
    )
    kinds = {m.kind for m in extract_modifier_matches(text)}
    assert CovenantModifierKind.AUDITOR_RECLASSIFICATION_INCLUDE in kinds
    assert CovenantModifierKind.AUDITOR_RECLASSIFICATION_EXCLUDE not in kinds


def test_dual_instruction_retains_separate_include_and_exclude() -> None:
    text = (
        "Reclassifications of type A accepted by the auditor shall be included. "
        "Amounts of type B reclassified as non-operating shall not be counted."
    )
    matches = extract_modifier_matches(text)
    kinds = {m.kind for m in matches}
    assert CovenantModifierKind.AUDITOR_RECLASSIFICATION_INCLUDE in kinds
    assert CovenantModifierKind.AUDITOR_RECLASSIFICATION_EXCLUDE in kinds
    include = next(
        m for m in matches if m.kind is CovenantModifierKind.AUDITOR_RECLASSIFICATION_INCLUDE
    )
    exclude = next(
        m for m in matches if m.kind is CovenantModifierKind.AUDITOR_RECLASSIFICATION_EXCLUDE
    )
    include_joined = " ".join(include.quotes).casefold()
    exclude_joined = " ".join(exclude.quotes).casefold()
    assert "included" in include_joined or "shall be included" in include_joined
    assert "not be counted" in exclude_joined or "shall not" in exclude_joined
    # Evidence regions must not be identical synthetic spans.
    assert include.quotes != exclude.quotes
