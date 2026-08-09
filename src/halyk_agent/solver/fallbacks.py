"""Fallbacks used when strict evaluation cannot fill a submission cell."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from halyk_agent.domain.covenant_evaluation import (
    CovenantEvaluationResult,
    EvaluationContext,
    EvaluationExecutor,
    EvaluationPlan,
    EvaluationReport,
    EvaluationStatus,
)
from halyk_agent.domain.covenants.ast import MetricCategory
from halyk_agent.domain.fact_extraction.text_locate import parse_money
from halyk_agent.domain.ids import deterministic_id
from halyk_agent.domain.transaction_taxonomy.amounts import sign_contract_for_category
from halyk_agent.domain.transaction_taxonomy.models import (
    AdjustmentEvent,
    AdjustmentEventType,
    CalculationInput,
    DefinitionReadinessEntry,
    EntityScopeKind,
    InputPeriodSemantics,
    InputSourceKind,
    RelatedPartyStatus,
    SelectorCoverageEntry,
    SelectorReadinessStatus,
)

_OPENING_RE = re.compile(r"Net book value at the beginning of the year\s*", re.I)
_DEPRECIATION_RE = re.compile(r"Depreciation charge for the year\s*", re.I)
_CLOSING_RE = re.compile(r"Net book value at the end of the year\s*", re.I)
_NO_DISPOSALS_RE = re.compile(
    r"There were no disposals of property, plant and equipment during the year", re.I
)
_NO_OTHER_MOVEMENTS_RE = re.compile(r"There were no other movements[^.\n]*", re.I)
_NOTE_HEADING_RE = re.compile(r"(?im)^\s*note\s+(?P<number>\d+)\b")
_OTHER_MOVEMENT_RE = re.compile(
    r"\b(?:"
    r"additions?|acquisitions?|business combinations?|"
    r"transfers?(?:\s+(?:in|out))?|revaluations?|impairments?|"
    r"foreign exchange(?:\s+movements?)?|fx movements?|"
    r"(?:currency\s+)?translation(?:\s+(?:differences?|movements?))?|"
    r"assets? held for sale|write[- ]?offs?|reclassifications?|"
    r"government grants?|capitali[sz]ed borrowing costs?|depletion|"
    r"right[- ]of[- ]use|other movements?"
    r")\b",
    re.I,
)


def _note_sections(full_text: str) -> tuple[str, ...]:
    """Split text on numbered note headings."""

    headings = list(_NOTE_HEADING_RE.finditer(full_text))
    sections: list[str] = []
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(full_text)
        sections.append(full_text[heading.start() : end])
    return tuple(sections)


def _money_after_label(note: str, match: re.Match[str]) -> tuple[Decimal, str] | None:
    # Stay on the same row. A broken value must not grab the amount from the next row.
    remainder = note[match.end() :]
    line = remainder.splitlines()[0] if remainder else ""
    return parse_money(line[:120])


@dataclass(frozen=True, slots=True)
class CompetitiveFallbackReport:
    """Fallback outputs and the diagnostics that explain them."""

    results: dict[tuple[str, str], CovenantEvaluationResult]
    context: EvaluationContext
    diagnostics: tuple[dict[str, Any], ...]
    eur_usd_rate: Decimal | None


def _settlement_eur_usd_rate(
    adjustments: tuple[AdjustmentEvent, ...],
) -> tuple[Decimal | None, tuple[str, ...]]:
    """Derive one unique source-backed EUR/USD settlement ratio across the corpus.

    This is competition-only evidence recovery, never a strict Stage 6 FX fact.
    Conflicting ratios fail closed.
    """

    candidates: dict[Decimal, set[str]] = {}
    for event in adjustments:
        if event.event_type is not AdjustmentEventType.FX_SETTLEMENT_REFERENCE:
            continue
        source = event.after.get("source_amount")
        settlement = event.after.get("settlement_amount")
        if not isinstance(source, dict) or not isinstance(settlement, dict):
            continue
        try:
            source_value = Decimal(str(source.get("value")))
            settlement_value = Decimal(str(settlement.get("value")))
        except Exception:
            continue
        if source_value <= 0 or settlement_value <= 0:
            continue
        source_currency = source.get("currency")
        settlement_currency = settlement.get("currency")
        if source_currency == "EUR" and settlement_currency == "USD":
            rate = settlement_value / source_value
        elif source_currency == "USD" and settlement_currency == "EUR":
            rate = source_value / settlement_value
        else:
            continue
        # Broad sanity guard only; the source ratio itself remains the evidence.
        if rate <= Decimal("0.5") or rate >= Decimal("2.0"):
            continue
        candidates.setdefault(rate, set()).update(event.evidence_span_ids)
    if len(candidates) != 1:
        return None, ()
    rate, evidence = next(iter(candidates.items()))
    return rate, tuple(sorted(evidence))


def _explicit_eur_usd_rate(
    adjustments: tuple[AdjustmentEvent, ...],
    *,
    scenario_id: str,
) -> tuple[Decimal | None, tuple[str, ...]]:
    """Return one explicit source-backed EUR/USD rate for this scenario."""

    candidates: dict[Decimal, set[str]] = {}
    for event in adjustments:
        if (
            event.event_type is not AdjustmentEventType.FX_SETTLEMENT_REFERENCE
            or event.scenario_id != scenario_id
            or event.after.get("rate_source") != "EXPLICIT"
        ):
            continue
        raw_rate = event.after.get("explicit_rate")
        from_currency = event.after.get("from_currency")
        to_currency = event.after.get("to_currency")
        if raw_rate in {None, ""}:
            continue
        try:
            rate = Decimal(str(raw_rate))
        except Exception:
            continue
        if rate <= 0:
            continue
        if from_currency == "EUR" and to_currency == "USD":
            eur_usd = rate
        elif from_currency == "USD" and to_currency == "EUR":
            eur_usd = Decimal("1") / rate
        else:
            continue
        candidates.setdefault(eur_usd, set()).update(event.evidence_span_ids)
    if len(candidates) != 1:
        return None, ()
    rate, evidence = next(iter(candidates.items()))
    return rate, tuple(sorted(evidence))


def _parsed_documents(parsed_dir: Path) -> dict[str, tuple[str, str]]:
    """Load document ids, filenames and flattened text from parse artifacts."""

    documents: dict[str, tuple[str, str]] = {}
    for path in sorted((parsed_dir / "documents").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        document_id = payload.get("document_id")
        source_file = payload.get("source_file")
        pages = payload.get("pages")
        if not isinstance(document_id, str) or not isinstance(source_file, str):
            continue
        if not isinstance(pages, list):
            continue
        text_parts: list[str] = []
        for page in pages:
            if not isinstance(page, dict):
                continue
            text = page.get("text") or page.get("raw_text")
            if isinstance(text, str):
                text_parts.append(text)
        documents[document_id] = (source_file, "\n".join(text_parts))
    return documents


def _group_documents_for_scenario(routing_dir: Path, scenario_id: str) -> tuple[str, ...]:
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


def _derive_group_capex(
    *,
    parsed_dir: Path,
    routing_dir: Path,
    scenario_id: str,
) -> tuple[Decimal | None, dict[str, Any] | None]:
    """Try to recover group capex from a complete PPE roll-forward."""

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

            # If the note names another movement, the residual is not safely attributable to capex.
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


def _convert_eur_inputs(
    inputs: tuple[CalculationInput, ...],
    *,
    rate: Decimal,
    scenario_id: str,
) -> tuple[CalculationInput, ...]:
    converted: list[CalculationInput] = []
    for item in inputs:
        if item.scenario_id != scenario_id or item.currency != "EUR":
            converted.append(item)
            continue
        update: dict[str, Any] = {
            "currency": "USD",
            "amount": item.amount * rate,
            "provenance_refs": tuple(
                (*item.provenance_refs, f"stage8:eur_usd_settlement_rate:{rate}")
            ),
        }
        if item.source_amount is not None:
            update["source_amount"] = item.source_amount * rate
        if item.metric_amount is not None:
            update["metric_amount"] = item.metric_amount * rate
        converted.append(item.model_copy(update=update))
    return tuple(converted)


def _group_capex_input(
    amount: Decimal,
    *,
    plan: EvaluationPlan,
    source_file: str,
) -> CalculationInput | None:
    semantics, sign_rule = sign_contract_for_category(MetricCategory.GROUP_CAPEX)
    start = plan.period.start_date
    end = plan.period.end_date
    if start is None or end is None:
        return None
    return CalculationInput(
        input_id=deterministic_id("stage8-fallback", plan.scenario_id, "GROUP_CAPEX", str(amount)),
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


def _enable_group_capex_selector(
    coverage: tuple[SelectorCoverageEntry, ...],
    readiness: tuple[DefinitionReadinessEntry, ...],
    *,
    definition_id: str,
) -> tuple[tuple[SelectorCoverageEntry, ...], tuple[DefinitionReadinessEntry, ...]]:
    updated_coverage: list[SelectorCoverageEntry] = []
    for entry in coverage:
        if entry.definition_id == definition_id and entry.category is MetricCategory.GROUP_CAPEX:
            updated_coverage.append(
                entry.model_copy(
                    update={
                        "status": SelectorReadinessStatus.READY,
                        "reason_code": "STAGE8_PPE_ROLL_FORWARD_RESIDUAL",
                        "matching_input_count": 1,
                    }
                )
            )
        else:
            updated_coverage.append(entry)
    updated_readiness: list[DefinitionReadinessEntry] = []
    for readiness_entry in readiness:
        if readiness_entry.definition_id == definition_id:
            updated_readiness.append(
                readiness_entry.model_copy(
                    update={
                        "status": SelectorReadinessStatus.READY,
                        "reason_code": "STAGE8_PPE_ROLL_FORWARD_RESIDUAL",
                        "unresolved_selectors": (),
                    }
                )
            )
        else:
            updated_readiness.append(readiness_entry)
    return tuple(updated_coverage), tuple(updated_readiness)


def _unique_group_capex_plan(
    plans: tuple[EvaluationPlan, ...],
    target_keys: set[tuple[str, str]],
) -> EvaluationPlan | None:
    """Find the one unresolved plan that asks for group capex."""

    candidates: list[EvaluationPlan] = []
    for plan in plans:
        key = (plan.scenario_id, plan.clause_id)
        if key not in target_keys:
            continue
        if any(
            node.selector is not None
            and node.selector.category is MetricCategory.GROUP_CAPEX
            and node.selector.group_level is True
            for node in plan.nodes
        ):
            candidates.append(plan)
    if len(candidates) != 1:
        return None
    return candidates[0]


def build_competitive_fallbacks(
    *,
    evaluation: EvaluationReport,
    context: EvaluationContext,
    adjustments: tuple[AdjustmentEvent, ...],
    parsed_dir: Path,
    routing_dir: Path,
) -> CompetitiveFallbackReport:
    """Retry unresolved cells with the small set of supported fallbacks."""

    strict_map = {(item.scenario_id, item.clause_id): item for item in evaluation.results}
    target_keys = {
        key
        for key, result in strict_map.items()
        if result.status not in {EvaluationStatus.RESOLVED, EvaluationStatus.NOT_ACTIVATED}
    }
    if not target_keys:
        return CompetitiveFallbackReport(
            results={}, context=context, diagnostics=(), eur_usd_rate=None
        )

    diagnostics: list[dict[str, Any]] = []
    working_inputs = context.calculation_inputs
    rate, rate_evidence = _settlement_eur_usd_rate(adjustments)
    if rate is not None:
        target_scenarios = sorted({scenario for scenario, _clause in target_keys})
        for scenario_id in target_scenarios:
            working_inputs = _convert_eur_inputs(
                working_inputs, rate=rate, scenario_id=scenario_id
            )
        diagnostics.append(
            {
                "strategy": "EUR_USD_SETTLEMENT_RATE_COMPETITIVE_FALLBACK",
                "rate": str(rate),
                "source_evidence_span_ids": list(rate_evidence),
                "scope": "unresolved-scenario EUR inputs only",
                "assumption": (
                    "competition-only reuse of the sole source-backed EUR/USD settlement ratio; "
                    "strict Stage 6 intentionally does not promote it to an FX fact"
                ),
            }
        )

    coverage = context.selector_coverage
    readiness = context.definition_readiness
    group_capex_plan = _unique_group_capex_plan(evaluation.plans, target_keys)
    if group_capex_plan is not None:
        group_capex_amount, group_capex_diagnostic = _derive_group_capex(
            parsed_dir=parsed_dir,
            routing_dir=routing_dir,
            scenario_id=group_capex_plan.scenario_id,
        )
        if group_capex_amount is not None and group_capex_diagnostic is not None:
            group_capex_input = _group_capex_input(
                group_capex_amount,
                plan=group_capex_plan,
                source_file=group_capex_diagnostic["source_file"],
            )
            if group_capex_input is not None:
                working_inputs = (*working_inputs, group_capex_input)
                coverage, readiness = _enable_group_capex_selector(
                    coverage,
                    readiness,
                    definition_id=group_capex_plan.definition_id,
                )
                diagnostics.append(group_capex_diagnostic)

    fallback_context = context.model_copy(
        update={
            "calculation_inputs": working_inputs,
            "selector_coverage": coverage,
            "definition_readiness": readiness,
        }
    )
    fallback_results = EvaluationExecutor().execute_many(evaluation.plans, fallback_context)
    by_key = {(item.scenario_id, item.clause_id): item for item in fallback_results}
    usable: dict[tuple[str, str], CovenantEvaluationResult] = {}
    for key in sorted(target_keys):
        result = by_key[key]
        if (
            result.status in {EvaluationStatus.RESOLVED, EvaluationStatus.NOT_ACTIVATED}
            and result.actual is not None
        ):
            usable[key] = result
            diagnostics.append(
                {
                    "strategy": "FALLBACK_CELL_EVALUATION",
                    "scenario_id": key[0],
                    "clause_id": key[1],
                    "status": result.status.value,
                    "compliance_status": (
                        result.compliance_status.value if result.compliance_status else None
                    ),
                    "actual": str(result.actual.value),
                    "issue_codes": [issue.code for issue in result.issues],
                }
            )

    return CompetitiveFallbackReport(
        results=usable,
        context=fallback_context,
        diagnostics=tuple(diagnostics),
        eur_usd_rate=rate,
    )
