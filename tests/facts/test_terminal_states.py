"""Terminal states, CONFIRMED_NONE, and model-eligibility regressions."""

from __future__ import annotations

from halyk_agent.domain.authority.models import AuthorityDomain
from halyk_agent.domain.fact_extraction.engine import run_fact_extraction
from halyk_agent.domain.fact_extraction.models import (
    FactKind,
    ReclassificationDisposition,
    RequirementTerminalState,
)
from halyk_agent.domain.models_gateway.gateway import LlmGatewayConfig, StructuredModelGateway
from halyk_agent.domain.models_gateway.providers.mock import MockStructuredProvider
from halyk_agent.domain.models_gateway.types import ExtractionState, StructuredExtractionResult
from tests.authority.helpers import make_document
from tests.facts.helpers import make_decision, make_definition, reclass_modifier


def test_confirmed_none_vs_rejected_reclass() -> None:
    none_doc = make_document(
        artifact="none",
        raw_text="Переклассификаций за ковенантный период не требовалось.",
    )
    rejected_doc = make_document(
        artifact="rej",
        sha="d" * 64,
        raw_text=(
            "Сумма в размере $10,000.00, выплаченная контрагенту Acme LLP, "
            "учтенная как OPEX, переклассифицирована как CAPEX была отклонена аудитором."
        ),
    )
    definitions = (make_definition(modifiers=(reclass_modifier(),)),)

    report_none = run_fact_extraction(
        definitions=definitions,
        decisions=(
            make_decision(
                domain=AuthorityDomain.FINANCIAL_ADJUSTMENTS,
                winning=(none_doc.document_id,),
            ),
        ),
        documents=(none_doc,),
    )
    results_none = {r.fact_kind: r for r in report_none.requirement_results}
    assert results_none[FactKind.TRANSACTION_RECLASSIFICATION].terminal_state is (
        RequirementTerminalState.CONFIRMED_NONE
    )
    assert results_none[FactKind.TRANSACTION_RECLASSIFICATION].model_eligible is False
    assert not any(
        f.fact_kind is FactKind.TRANSACTION_RECLASSIFICATION for f in report_none.accepted_facts
    )
    assert results_none[FactKind.TRANSACTION_RECLASSIFICATION].evidence_span_ids

    report_rej = run_fact_extraction(
        definitions=definitions,
        decisions=(
            make_decision(
                domain=AuthorityDomain.FINANCIAL_ADJUSTMENTS,
                winning=(rejected_doc.document_id,),
            ),
        ),
        documents=(rejected_doc,),
    )
    assert any(
        f.fact_kind is FactKind.TRANSACTION_RECLASSIFICATION
        and getattr(f.payload, "disposition", None) is ReclassificationDisposition.REJECTED
        for f in report_rej.accepted_facts
    )
    rej_result = next(
        r
        for r in report_rej.requirement_results
        if r.fact_kind is FactKind.TRANSACTION_RECLASSIFICATION
    )
    assert rej_result.terminal_state is RequirementTerminalState.RESOLVED


def test_confirmed_none_does_not_call_model() -> None:
    doc = make_document(
        raw_text="No reclassification was required for the covenant period.",
    )
    mock = MockStructuredProvider(
        default_factory=lambda _r: StructuredExtractionResult(
            state=ExtractionState.RESOLVED,
            payload={"from_category": "A", "to_category": "B"},
            evidence_fragment_ids=("F001",),
            quote="No reclassification was required",
            page_number=1,
            char_start=0,
            char_end=10,
            reason_code="SHOULD_NOT_RUN",
        )
    )
    gw = StructuredModelGateway(
        config=LlmGatewayConfig(
            allow_network=True,
            primary_provider="mock",
            max_external_attempts=8,
        ),
        mock=mock,
    )
    report = run_fact_extraction(
        definitions=(make_definition(modifiers=(reclass_modifier(),)),),
        decisions=(
            make_decision(
                domain=AuthorityDomain.FINANCIAL_ADJUSTMENTS,
                winning=(doc.document_id,),
            ),
        ),
        documents=(doc,),
        allow_network_models=True,
        model_gateway=gw,
    )
    assert not mock.calls
    assert report.manifest.model_call_count == 0
    assert any(
        r.terminal_state is RequirementTerminalState.CONFIRMED_NONE
        for r in report.requirement_results
    )


def test_every_requirement_has_terminal_state() -> None:
    doc = make_document(raw_text="Revenue note without reclass cues.")
    report = run_fact_extraction(
        definitions=(make_definition(modifiers=(reclass_modifier(),)),),
        decisions=(
            make_decision(
                domain=AuthorityDomain.FINANCIAL_ADJUSTMENTS,
                winning=(doc.document_id,),
            ),
        ),
        documents=(doc,),
    )
    assert len(report.requirement_results) == len(report.requirements)
    assert {r.requirement_id for r in report.requirement_results} == {
        r.requirement_id for r in report.requirements
    }
    assert report.manifest.speculative_count == 0


def test_subsidiary_requirement_kept_without_group_authority() -> None:
    from halyk_agent.domain.covenants.ast import MetricCategory, TransactionSelector
    from halyk_agent.domain.fact_extraction.requirements import derive_fact_requirements

    definitions = (
        make_definition(
            selectors=(
                TransactionSelector(
                    category=MetricCategory.CAPITAL_ASSET_TRANSFERS_TO_UNRESTRICTED_SUBS,
                ),
            )
        ),
    )
    reqs = derive_fact_requirements(definitions, ())
    assert any(r.fact_kind is FactKind.SUBSIDIARY_STATUS for r in reqs)
    report = run_fact_extraction(
        definitions=definitions,
        decisions=(),
        documents=(),
    )
    sub = next(r for r in report.requirement_results if r.fact_kind is FactKind.SUBSIDIARY_STATUS)
    assert sub.terminal_state is RequirementTerminalState.NOT_APPLICABLE
