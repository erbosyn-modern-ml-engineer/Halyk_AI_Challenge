"""Typed MATERIALITY_FLOOR threshold contract (Stage 6 pre-flight)."""

# ruff: noqa: RUF001

from __future__ import annotations

from decimal import Decimal

import pytest

from halyk_agent.domain.authority.models import (
    AuthorityDecision,
    AuthorityDomain,
    AuthorityStatus,
)
from halyk_agent.domain.covenants.ast import MetricCategory
from halyk_agent.domain.covenants.engine import run_covenant_compile
from halyk_agent.domain.covenants.models import CovenantModifierKind
from halyk_agent.domain.covenants.modifiers import extract_modifier_matches
from halyk_agent.domain.covenants.quantity import QuantityType
from tests.authority.helpers import make_document


@pytest.mark.parametrize(
    ("text", "value", "currency"),
    [
        (
            "Разовыми для целей ковенантов признаются статьи в сумме не менее $300,000.00; "
            "статьи меньшей суммы к EBITDA не прибавляются.",
            Decimal("300000.00"),
            "USD",
        ),
        (
            "One-time add-backs are recognized only when not less than $150,000.00.",
            Decimal("150000.00"),
            "USD",
        ),
        (
            "Разовыми признаются статьи в сумме не менее EUR 100,000.00; "
            "статьи меньшей суммы к EBITDA не прибавляются.",
            Decimal("100000.00"),
            "EUR",
        ),
        (
            "Разовыми признаются статьи в сумме не менее ₸500,000.00; "
            "статьи меньшей суммы к EBITDA не прибавляются.",
            Decimal("500000.00"),
            "KZT",
        ),
    ],
)
def test_materiality_floor_parses_typed_threshold(text: str, value: Decimal, currency: str) -> None:
    matches = extract_modifier_matches(text)
    floor = next(m for m in matches if m.kind is CovenantModifierKind.MATERIALITY_FLOOR)
    assert floor.threshold is not None
    assert floor.threshold.quantity_type is QuantityType.MONEY
    assert floor.threshold.value == value
    assert floor.threshold.currency == currency
    assert floor.applies_to_category is MetricCategory.ONE_TIME_ADD_BACKS


@pytest.mark.parametrize(
    "text",
    [
        "С учётом порога существенности аудитора статьи к добавлению не принимаются.",
        "subject to auditor materiality, add-backs may be excluded",
        "Разовыми признаются статьи в сумме не менее; к EBITDA не прибавляются.",
        "не менее $1,00,0.0.0 к EBITDA не прибавляются для разовых статей",
    ],
)
def test_materiality_without_unambiguous_threshold_fails_closed(text: str) -> None:
    matches = extract_modifier_matches(text)
    assert all(m.kind is not CovenantModifierKind.MATERIALITY_FLOOR for m in matches)


def test_fa_document_attaches_typed_materiality_to_add_back_definition() -> None:
    loan = (
        "Пункт 6.1 Adjusted. Заёмщик обязуется поддерживать отношение "
        "скорректированной EBITDA к выручке за период с 2025-01-01 по 2025-12-31 "
        "на уровне не менее 0.18x, с учётом разовых корректировок."
    )
    fa = (
        "Ниже приведены разовые статьи.\n"
        "Разовыми для целей ковенантов признаются статьи в сумме не менее $300,000.00; "
        "статьи меньшей суммы к EBITDA не прибавляются."
    )
    loan_doc = make_document(raw_text=loan, sha="a" * 64)
    fa_doc = make_document(raw_text=fa, sha="b" * 64)
    report = run_covenant_compile(
        template_answers={"P4": {"6.1": {}}},
        decisions=(
            AuthorityDecision(
                decision_id="d-loan",
                scenario_id="P4",
                domain=AuthorityDomain.COVENANT_TERMS,
                status=AuthorityStatus.AUTHORITATIVE,
                rule_id="RULE_TEST",
                reason="test",
                winning_document_ids=(loan_doc.document_id,),
                rejected_document_ids=(),
                candidate_document_ids=(loan_doc.document_id,),
            ),
            AuthorityDecision(
                decision_id="d-fa",
                scenario_id="P4",
                domain=AuthorityDomain.FINANCIAL_ADJUSTMENTS,
                status=AuthorityStatus.AUTHORITATIVE,
                rule_id="RULE_TEST",
                reason="test",
                winning_document_ids=(fa_doc.document_id,),
                rejected_document_ids=(),
                candidate_document_ids=(fa_doc.document_id,),
            ),
        ),
        documents=(loan_doc, fa_doc),
        authority_manifest_hash="1" * 64,
    )
    assert report.manifest.definition_count == 1
    definition = report.definitions[0]
    floor = next(
        m for m in definition.modifiers if m.kind is CovenantModifierKind.MATERIALITY_FLOOR
    )
    assert floor.threshold is not None
    assert floor.threshold.value == Decimal("300000.00")
    assert floor.threshold.currency == "USD"
    assert floor.applies_to_category is MetricCategory.ONE_TIME_ADD_BACKS
    assert floor.evidence_span_ids
