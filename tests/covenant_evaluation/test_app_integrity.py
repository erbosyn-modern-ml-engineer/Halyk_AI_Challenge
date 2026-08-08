"""Artifact-integrity and deterministic-publication tests for Stage 6."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from halyk_agent.adapters.evaluation.io import hash_covenant_definitions
from halyk_agent.app.evaluation import EvaluationServiceError, evaluate_from_paths
from halyk_agent.domain.covenants.ast import (
    MetricCategory,
    Sum,
    TransactionSelector,
    TransactionSet,
)
from halyk_agent.domain.covenants.models import (
    Comparator,
    CovenantDefinition,
    CovenantEvidenceRefs,
    CovenantManifest,
    PeriodDefinition,
    PeriodKind,
    ScopeDefinition,
    ScopeKind,
    ScopeProvenance,
)
from halyk_agent.domain.covenants.quantity import QuantityType, TypedQuantity
from halyk_agent.domain.ids import sha256_text
from halyk_agent.domain.transaction_taxonomy.engine import hash_taxonomy_models
from halyk_agent.domain.transaction_taxonomy.models import (
    AMOUNT_CONTRACT_VERSION,
    CalculationInput,
    DefinitionReadinessEntry,
    EntityScopeKind,
    InputPeriodSemantics,
    InputSourceKind,
    RelatedPartyStatus,
    SelectorCoverageEntry,
    SelectorReadinessStatus,
    TaxonomyManifest,
)


def _definition() -> CovenantDefinition:
    selector = TransactionSelector(category=MetricCategory.REVENUE)
    return CovenantDefinition(
        definition_id="def-1",
        scenario_id="S1",
        clause_id="6.1",
        document_id="doc-1",
        document_version_id="docv-1",
        source_file="loan.pdf",
        source_sha256="a" * 64,
        family_id="MIN_REVENUE",
        metric=Sum(of=TransactionSet(selector=selector)),
        metric_quantity_type=QuantityType.MONEY,
        comparator=Comparator.GTE,
        threshold=TypedQuantity(
            quantity_type=QuantityType.MONEY,
            value=Decimal("100"),
            currency="USD",
        ),
        period=PeriodDefinition(
            period_kind=PeriodKind.CLOSED_INTERVAL,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
        ),
        scope=ScopeDefinition(
            scope_kind=ScopeKind.BORROWER,
            provenance=ScopeProvenance.DEFAULT_BORROWER_BY_RULE,
        ),
        selectors=(selector,),
        evidence=CovenantEvidenceRefs(),
        rendered="synthetic",
    )


def _input() -> CalculationInput:
    return CalculationInput(
        input_id="input-1",
        scenario_id="S1",
        source_kind=InputSourceKind.LEDGER_ROW,
        transaction_id="TX-S1-1",
        category=MetricCategory.REVENUE,
        selector_categories=(MetricCategory.REVENUE,),
        amount=Decimal("125.50"),
        source_amount=Decimal("125.50"),
        metric_amount=Decimal("125.50"),
        currency="USD",
        period_semantics=InputPeriodSemantics.FLOW,
        transaction_date=date(2025, 6, 1),
        related_party=RelatedPartyStatus.FALSE,
        entity_scope=EntityScopeKind.BORROWER,
    )


def _write_bundle(root: Path) -> tuple[Path, Path]:
    covenants_dir = root / "covenants"
    transactions_dir = root / "transactions"
    covenants_dir.mkdir(parents=True)
    transactions_dir.mkdir(parents=True)

    definition = _definition()
    definitions = (definition,)
    covenant_manifest = CovenantManifest(
        authority_manifest_hash="auth",
        template_answers_hash="template",
        canonical_documents_hash="canonical",
        scenario_count=1,
        cell_count=1,
        authoritative_covenant_docs=1,
        definition_count=1,
        supported_count=1,
        unsupported_count=0,
        failure_count=0,
        evidence_span_count=0,
        evidence_hash="evidence",
        definitions_hash=hash_covenant_definitions(definitions),
    )
    covenant_manifest_text = covenant_manifest.model_dump_json(indent=2) + "\n"
    (covenants_dir / "covenant_manifest.json").write_text(
        covenant_manifest_text,
        encoding="utf-8",
    )
    (covenants_dir / "covenant_definitions.jsonl").write_text(
        json.dumps(definition.model_dump(mode="json"), sort_keys=True) + "\n",
        encoding="utf-8",
    )

    inputs = (_input(),)
    coverage = (
        SelectorCoverageEntry(
            definition_id="def-1",
            scenario_id="S1",
            category=MetricCategory.REVENUE,
            related_party_only=False,
            group_level=False,
            status=SelectorReadinessStatus.READY,
            reason_code="OK",
            matching_input_count=1,
        ),
    )
    readiness = (
        DefinitionReadinessEntry(
            definition_id="def-1",
            scenario_id="S1",
            status=SelectorReadinessStatus.READY,
            reason_code="OK",
        ),
    )
    manifest = TaxonomyManifest(
        routing_manifest_hash="routing",
        covenant_manifest_hash=sha256_text(covenant_manifest_text),
        facts_manifest_hash="facts",
        ledger_source_sha256="b" * 64,
        scenario_count=1,
        ledger_row_count=1,
        scenario_linked_count=1,
        routing_noise_count=0,
        classified_count=1,
        unresolved_count=0,
        conflict_count=0,
        irrelevant_count=0,
        calculation_input_count=1,
        derived_input_count=0,
        adjustment_event_count=0,
        selector_count=1,
        selector_ready_count=1,
        selector_true_zero_count=0,
        selector_unresolved_count=0,
        selector_supported_count=1,
        selector_unsupported_count=0,
        definition_ready_count=1,
        definition_unresolved_count=0,
        amount_contract_version=AMOUNT_CONTRACT_VERSION,
        accepted_facts_count=0,
        facts_consumed_count=0,
        related_party_true_count=0,
        related_party_false_count=1,
        related_party_unknown_count=0,
        taxonomy_hash="taxonomy",
        calculation_inputs_hash=hash_taxonomy_models(inputs),
        adjustments_hash="adjustments",
        selector_coverage_hash=hash_taxonomy_models(coverage),
        definition_readiness_hash=hash_taxonomy_models(readiness),
    )
    (transactions_dir / "stage5f_manifest.json").write_text(
        manifest.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    (transactions_dir / "calculation_inputs.jsonl").write_text(
        json.dumps(inputs[0].model_dump(mode="json"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (transactions_dir / "selector_coverage.json").write_text(
        json.dumps([item.model_dump(mode="json") for item in coverage], indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    (transactions_dir / "definition_readiness.json").write_text(
        json.dumps([item.model_dump(mode="json") for item in readiness], indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return covenants_dir, transactions_dir


def test_publication_is_byte_deterministic(tmp_path: Path) -> None:
    covenants_dir, transactions_dir = _write_bundle(tmp_path)
    first_dir = tmp_path / "evaluation-a"
    second_dir = tmp_path / "evaluation-b"

    first = evaluate_from_paths(
        covenants_dir=covenants_dir,
        transactions_dir=transactions_dir,
        output_dir=first_dir,
    )
    second = evaluate_from_paths(
        covenants_dir=covenants_dir,
        transactions_dir=transactions_dir,
        output_dir=second_dir,
    )

    assert first == second
    assert first.manifest.resolved_count == 1
    assert first.results[0].actual is not None
    assert first.results[0].actual.value == Decimal("125.50")
    for name in (
        "evaluation_manifest.json",
        "evaluation_plans.jsonl",
        "covenant_evaluations.jsonl",
        "evaluation_summary.md",
    ):
        assert (first_dir / name).read_bytes() == (second_dir / name).read_bytes()


def test_tampered_calculation_inputs_fail_before_publication(tmp_path: Path) -> None:
    covenants_dir, transactions_dir = _write_bundle(tmp_path)
    input_path = transactions_dir / "calculation_inputs.jsonl"
    input_path.write_text(
        input_path.read_text(encoding="utf-8").replace("125.50", "999.99"),
        encoding="utf-8",
    )
    output_dir = tmp_path / "evaluation"
    with pytest.raises(EvaluationServiceError) as exc:
        evaluate_from_paths(
            covenants_dir=covenants_dir,
            transactions_dir=transactions_dir,
            output_dir=output_dir,
        )
    assert exc.value.code == "CALCULATION_INPUTS_HASH_MISMATCH"
    assert not output_dir.exists()
