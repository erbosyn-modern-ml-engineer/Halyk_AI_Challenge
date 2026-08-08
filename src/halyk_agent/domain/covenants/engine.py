"""Stage 5D covenant compilation engine."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from halyk_agent.domain.authority.models import (
    AuthorityDecision,
    AuthorityDomain,
    AuthorityStatus,
)
from halyk_agent.domain.covenants.ast import MetricCategory
from halyk_agent.domain.covenants.compiler import compile_covenant_cell
from halyk_agent.domain.covenants.constants import (
    COVENANT_ALGORITHM_VERSION,
    COVENANT_COMPILER_VERSION,
    COVENANT_RULE_VERSION,
    COVENANT_SCHEMA_VERSION,
)
from halyk_agent.domain.covenants.locate import find_quote_in_document, join_document_text
from halyk_agent.domain.covenants.models import (
    CompileStatus,
    CovenantCompileFailure,
    CovenantDefinition,
    CovenantManifest,
    CovenantModifier,
    CovenantModifierKind,
    CovenantReport,
)
from halyk_agent.domain.covenants.modifiers import extract_modifier_matches
from halyk_agent.domain.covenants.render import render_covenant_definition
from halyk_agent.domain.evidence import EvidenceSpan
from halyk_agent.domain.ids import deterministic_id, sha256_text
from halyk_agent.domain.parsing import CanonicalDocument
from halyk_agent.domain.routing.scenarios import discover_scenarios, template_cell_count


def _hash_json(payload: Mapping[str, Any] | list[Any] | str) -> str:
    if isinstance(payload, str):
        return sha256_text(payload)
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_text(text)


def _covenant_winners(
    decisions: tuple[AuthorityDecision, ...] | list[AuthorityDecision],
) -> dict[str, str]:
    winners: dict[str, str] = {}
    for decision in decisions:
        if (
            decision.domain is AuthorityDomain.COVENANT_TERMS
            and decision.status is AuthorityStatus.AUTHORITATIVE
            and decision.winning_document_ids
        ):
            winners[decision.scenario_id] = decision.winning_document_ids[0]
    return winners


def _financial_adjustment_winners(
    decisions: tuple[AuthorityDecision, ...] | list[AuthorityDecision],
) -> dict[str, str]:
    winners: dict[str, str] = {}
    for decision in decisions:
        if (
            decision.domain is AuthorityDomain.FINANCIAL_ADJUSTMENTS
            and decision.status is AuthorityStatus.AUTHORITATIVE
            and decision.winning_document_ids
        ):
            winners[decision.scenario_id] = decision.winning_document_ids[0]
    return winners


def _typed_materiality_from_document(
    document: CanonicalDocument,
) -> tuple[CovenantModifier | None, tuple[EvidenceSpan, ...]]:
    """
    Extract a typed MATERIALITY_FLOOR from an authoritative adjustments document.

    The modifier is generated only from clause text that itself carries an
    unambiguous monetary threshold (fail closed otherwise).
    """
    joined, _cursors = join_document_text(document)
    matches = [
        m
        for m in extract_modifier_matches(joined)
        if m.kind is CovenantModifierKind.MATERIALITY_FLOOR and m.threshold is not None
    ]
    if len(matches) != 1:
        return None, ()
    match = matches[0]
    spans: list[EvidenceSpan] = []
    span_ids: list[str] = []
    for quote in match.quotes:
        span = find_quote_in_document(document, needle=quote)
        if span is not None:
            spans.append(span)
            span_ids.append(span.id)
    if not span_ids:
        return None, ()
    modifier = CovenantModifier(
        kind=match.kind,
        detail=match.detail,
        evidence_span_ids=tuple(dict.fromkeys(span_ids)),
        threshold=match.threshold,
        applies_to_category=match.applies_to_category,
    )
    return modifier, tuple(spans)


def _definition_needs_add_back_materiality(definition: CovenantDefinition) -> bool:
    return any(s.category is MetricCategory.ONE_TIME_ADD_BACKS for s in definition.selectors)


def _definition_has_typed_materiality(definition: CovenantDefinition) -> bool:
    return any(
        m.kind is CovenantModifierKind.MATERIALITY_FLOOR and m.threshold is not None
        for m in definition.modifiers
    )


def _attach_financial_materiality(
    definition: CovenantDefinition,
    *,
    fa_document: CanonicalDocument,
) -> tuple[CovenantDefinition, tuple[EvidenceSpan, ...]]:
    """Attach FA-sourced typed materiality when the covenant cell needs add-back floors."""
    if not _definition_needs_add_back_materiality(definition):
        return definition, ()
    if _definition_has_typed_materiality(definition):
        return definition, ()
    modifier, spans = _typed_materiality_from_document(fa_document)
    if modifier is None:
        return definition, ()
    # Drop any incomplete same-kind residue (should already be absent after fail-closed parse).
    kept = tuple(
        m for m in definition.modifiers if m.kind is not CovenantModifierKind.MATERIALITY_FLOOR
    )
    evidence = definition.evidence.model_copy(
        update={
            "modifier_span_ids": tuple(
                dict.fromkeys((*definition.evidence.modifier_span_ids, *modifier.evidence_span_ids))
            )
        }
    )
    updated = definition.model_copy(
        update={
            "modifiers": (*kept, modifier),
            "evidence": evidence,
            "rendered": "pending",
        }
    )
    updated = updated.model_copy(update={"rendered": render_covenant_definition(updated)})
    return updated, spans


def run_covenant_compile(
    *,
    template_answers: Mapping[str, Any],
    decisions: tuple[AuthorityDecision, ...] | list[AuthorityDecision],
    documents: tuple[CanonicalDocument, ...] | list[CanonicalDocument],
    authority_manifest_hash: str,
) -> CovenantReport:
    """
    Compile typed CovenantDefinitions for every template covenant cell.

    Consumes Stage 5C authority decisions + OCR-enriched canonical documents.
    Does not read ground truth. Does not calculate ledger actuals.
    """
    scenarios = discover_scenarios(template_answers)
    docs_by_id = {doc.document_id: doc for doc in documents}
    winners = _covenant_winners(decisions)
    fa_winners = _financial_adjustment_winners(decisions)

    definitions: list[CovenantDefinition] = []
    failures: list[CovenantCompileFailure] = []
    spans: list[EvidenceSpan] = []

    for scenario in scenarios:
        winner_id = winners.get(scenario.scenario_id)
        if winner_id is None:
            for clause_id in scenario.required_covenant_ids:
                failures.append(
                    CovenantCompileFailure(
                        failure_id=deterministic_id(
                            "covenant-failure-v1",
                            scenario.scenario_id,
                            clause_id,
                            "missing-authority",
                        ),
                        scenario_id=scenario.scenario_id,
                        clause_id=clause_id,
                        status=CompileStatus.MISSING_AUTHORITY,
                        reason="no authoritative COVENANT_TERMS document for scenario",
                    )
                )
            continue
        document = docs_by_id.get(winner_id)
        if document is None:
            for clause_id in scenario.required_covenant_ids:
                failures.append(
                    CovenantCompileFailure(
                        failure_id=deterministic_id(
                            "covenant-failure-v1",
                            scenario.scenario_id,
                            clause_id,
                            "missing-doc",
                        ),
                        scenario_id=scenario.scenario_id,
                        clause_id=clause_id,
                        status=CompileStatus.MISSING_EVIDENCE,
                        reason="authoritative document not present in parsed inputs",
                        document_id=winner_id,
                    )
                )
            continue
        fa_doc_id = fa_winners.get(scenario.scenario_id)
        fa_document = docs_by_id.get(fa_doc_id) if fa_doc_id else None
        for clause_id in scenario.required_covenant_ids:
            definition, failure, cell_spans = compile_covenant_cell(
                scenario_id=scenario.scenario_id,
                clause_id=clause_id,
                document=document,
            )
            spans.extend(cell_spans)
            if definition is not None and fa_document is not None:
                definition, fa_spans = _attach_financial_materiality(
                    definition, fa_document=fa_document
                )
                spans.extend(fa_spans)
            if definition is not None:
                definitions.append(definition)
            if failure is not None:
                failures.append(failure)

    definitions_sorted = tuple(
        sorted(definitions, key=lambda item: (item.scenario_id, item.clause_id, item.definition_id))
    )
    failures_sorted = tuple(
        sorted(failures, key=lambda item: (item.scenario_id, item.clause_id, item.failure_id))
    )
    spans_sorted = tuple(sorted({span.id: span for span in spans}.values(), key=lambda s: s.id))

    docs_used = sorted({item.document_id for item in definitions_sorted})
    canonical_hash = _hash_json(
        [
            {
                "document_id": docs_by_id[doc_id].document_id,
                "source_sha256": docs_by_id[doc_id].source_sha256,
                "document_version_id": docs_by_id[doc_id].document_version_id,
            }
            for doc_id in docs_used
            if doc_id in docs_by_id
        ]
    )
    definitions_hash = _hash_json([item.model_dump(mode="json") for item in definitions_sorted])
    evidence_hash = _hash_json([span.model_dump(mode="json") for span in spans_sorted])
    template_hash = _hash_json(dict(template_answers))

    manifest = CovenantManifest(
        schema_version=COVENANT_SCHEMA_VERSION,
        compiler_version=COVENANT_COMPILER_VERSION,
        rule_version=COVENANT_RULE_VERSION,
        authority_manifest_hash=authority_manifest_hash,
        template_answers_hash=template_hash,
        canonical_documents_hash=canonical_hash,
        scenario_count=len(scenarios),
        cell_count=template_cell_count(scenarios),
        authoritative_covenant_docs=len(docs_used),
        definition_count=len(definitions_sorted),
        supported_count=len(definitions_sorted),
        unsupported_count=sum(
            1 for item in failures_sorted if item.status is not CompileStatus.SUPPORTED
        ),
        failure_count=len(failures_sorted),
        evidence_span_count=len(spans_sorted),
        evidence_hash=evidence_hash,
        definitions_hash=definitions_hash,
    )
    # Keep algorithm version referenced for identity without changing schema.
    _ = COVENANT_ALGORITHM_VERSION
    return CovenantReport(
        manifest=manifest,
        definitions=definitions_sorted,
        failures=failures_sorted,
        spans=spans_sorted,
    )
