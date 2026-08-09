"""Bounded DeepSeek semantic planner for covenant clauses (Stage 5D, DSL v2).

The model interprets contractual *language* into a typed candidate plan. It never
calculates, never decides compliance, and never chooses an authoritative document.
Every candidate must survive local schema, enum, type and exact-quote validation
before it becomes a plan; otherwise the cell stays UNRESOLVED.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from halyk_agent.config import Settings
from halyk_agent.domain.covenants.ast import (
    BOOL_EXPR_ADAPTER,
    EXPR_ADAPTER,
    AccountingScope,
    Comparator,
    MetricCategory,
    PeriodBasis,
    PeriodGrouping,
    PeriodReducer,
    infer_quantity_type,
)
from halyk_agent.domain.covenants.models import CovenantPlan
from halyk_agent.domain.covenants.plans import finalize_plan
from halyk_agent.domain.covenants.quantity import CovenantTypeError
from halyk_agent.domain.ids import sha256_text
from halyk_agent.domain.models_gateway.semantic_json import SemanticJsonGateway, SemanticJsonState

NUMERIC_NODE_KINDS: tuple[str, ...] = (
    "constant",
    "transaction_set",
    "sum",
    "count",
    "min",
    "max",
    "add",
    "subtract",
    "multiply",
    "divide",
    "period_aggregate",
)

BOOLEAN_NODE_KINDS: tuple[str, ...] = ("compare", "and", "or", "not", "always")


class _PlanCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reported_actual: dict[str, Any]
    activation_condition: dict[str, Any]
    breach_condition: dict[str, Any]
    period_basis: str = PeriodBasis.CASH_DATE.value
    period_grouping: str | None = None
    source_quotes: list[str]
    confidence: Literal["HIGH", "MEDIUM", "LOW"]
    reason: str = ""


@dataclass(frozen=True, slots=True)
class SemanticPlanResult:
    plan: CovenantPlan | None
    diagnostic: dict[str, Any]
    model_called: bool


_SYSTEM = (
    "You are a bounded legal-financial semantic parser for loan covenants. The "
    "deterministic parser could not represent this clause. Translate the clause "
    "LANGUAGE into a typed covenant plan using ONLY the supplied node kinds, "
    "metric categories, scopes and enums.\n"
    "\n"
    "Model exactly three things:\n"
    "1. reported_actual — the numeric expression the report must state.\n"
    "2. activation_condition — whether the restriction applies at all. Use "
    '{"kind":"always"} when the covenant is unconditional. A springing covenant '
    "puts its trigger here, NOT in the breach condition.\n"
    "3. breach_condition — what makes the covenant breached once active. State it "
    "as the BREACH direction, not the compliance direction.\n"
    "\n"
    "Rules you must not violate:\n"
    "- Never calculate a value, a ratio, an actual, or a compliance status.\n"
    "- Never invent transaction identifiers, dates, FX rates or amounts that are "
    "not written in the clause.\n"
    "- A threshold may be an expression (for example 5% of group CAPEX). Use a "
    "compare node with that expression on the right; do not invent a number.\n"
    "- Scope is part of metric identity. Group/parent/subsidiary metrics must set "
    "the selector scope; never substitute a borrower metric for a group metric.\n"
    "- If the clause needs a statement line that is not in the category list, do "
    "NOT map it to a merely similar category. Return LOW confidence instead.\n"
    "- 'every quarter'/'any quarter' style requirements use period_aggregate with "
    "reducer MAX for ceilings and MIN for floors.\n"
    "- source_quotes must be exact contiguous substrings copied from the clause "
    "text, covering the metric, the operator and the threshold.\n"
    "- If the plan cannot be expressed exactly with this DSL, return LOW confidence "
    "and say which construct is missing in reason.\n"
    "\n"
    "Return one flat JSON object whose only top-level keys are reported_actual, "
    "activation_condition, breach_condition, period_basis, period_grouping, "
    "source_quotes, confidence and reason. Do not wrap it in another object and do "
    "not echo the request back."
)


def _schema_hint() -> dict[str, Any]:
    return {
        "numeric_node_kinds": list(NUMERIC_NODE_KINDS),
        "boolean_node_kinds": list(BOOLEAN_NODE_KINDS),
        "metric_categories": [item.value for item in MetricCategory],
        "accounting_scopes": [item.value for item in AccountingScope],
        "comparators": [item.value for item in Comparator],
        "period_groupings": [item.value for item in PeriodGrouping],
        "period_reducers": [item.value for item in PeriodReducer],
        "period_bases": [item.value for item in PeriodBasis],
        "node_shapes": {
            "constant": {
                "kind": "constant",
                "quantity": {
                    "quantity_type": "MONEY|RATIO|PERCENT|COUNT",
                    "value": "decimal string",
                    "currency": "ISO code, MONEY only",
                },
            },
            "transaction_set": {
                "kind": "transaction_set",
                "selector": {
                    "category": "one metric category",
                    "scope": "one accounting scope",
                    "related_party_only": "bool",
                },
            },
            "sum": {"kind": "sum", "of": "numeric node"},
            "min": {"kind": "min", "args": ["numeric node", "numeric node"]},
            "max": {"kind": "max", "args": ["numeric node", "numeric node"]},
            "add": {"kind": "add", "left": "numeric", "right": "numeric"},
            "subtract": {"kind": "subtract", "left": "numeric", "right": "numeric"},
            "multiply": {"kind": "multiply", "left": "numeric", "right": "numeric"},
            "divide": {"kind": "divide", "numerator": "numeric", "denominator": "numeric"},
            "period_aggregate": {
                "kind": "period_aggregate",
                "of": "numeric node",
                "grouping": "period grouping",
                "reducer": "period reducer",
                "basis": "period basis",
            },
            "compare": {
                "kind": "compare",
                "left": "numeric",
                "comparator": "one comparator",
                "right": "numeric",
            },
            "and": {"kind": "and", "args": ["boolean", "boolean"]},
            "or": {"kind": "or", "args": ["boolean", "boolean"]},
            "not": {"kind": "not", "of": "boolean"},
            "always": {"kind": "always"},
        },
        "recipes": {
            "EBITDA": "subtract(sum(REVENUE), sum(OPEX))",
            "EBITDAR (EBITDA plus rent)": "add(<EBITDA>, sum(RENT))",
            "fixed charges (interest plus rent)": "add(sum(INTEREST_EXPENSE), sum(RENT))",
            "fixed charge cover": "divide(<EBITDAR>, <fixed charges>)",
            "DSCR": (
                "divide(<EBITDA>, add(sum(INTEREST_EXPENSE), sum(SCHEDULED_PRINCIPAL_REPAYMENT)))"
            ),
            "adjusted debt": (
                "add(add(sum(FINANCIAL_DEBT), sum(GUARANTEE_LIABILITY)), sum(INDEMNITY_LIABILITY))"
            ),
            "capped add-back": (
                "min(sum(ONE_TIME_ADD_BACKS), multiply(constant RATIO, sum(REVENUE)))"
            ),
            "adjusted EBITDA with capped add-backs": "add(<EBITDA>, <capped add-back>)",
            "basket net of cap": "subtract(A, min(B, constant))",
            "worst quarter of a ceiling": "period_aggregate(of=sum(X), reducer=MAX)",
            "weakest quarter of a floor": "period_aggregate(of=sum(X), reducer=MIN)",
            "group metric outside the borrower": (
                "subtract(sum(X, scope=GROUP), sum(X, scope=BORROWER))"
            ),
            "percentage-of-metric threshold": "multiply(constant RATIO, sum(Y))",
        },
        "guidance": [
            "A derived metric has no dedicated node: compose it from the recipes. "
            "EBITDA, EBITDAR, DSCR and adjusted debt are all composable, so a clause "
            "using them is expressible and should be HIGH confidence.",
            "When the clause defines a term itself (for example 'total debt means "
            "financing drawn during the period'), model the clause's own definition "
            "rather than a general-purpose reading of the term.",
            "Only return LOW confidence when a required statement line has no matching "
            "metric category, or when the logic genuinely cannot be built from the "
            "supplied nodes. Say precisely which construct is missing.",
            "The measurement date range is parsed separately and is NOT part of this "
            "plan. Never lower confidence because dates cannot be expressed. Use "
            "period_grouping FULL_PERIOD when the covenant measures the whole period "
            "and FINANCIAL_QUARTER only when it genuinely speaks about quarters.",
        ],
        "output": {
            "reported_actual": "numeric node",
            "activation_condition": "boolean node",
            "breach_condition": "boolean node",
            "period_basis": "period basis",
            "period_grouping": "period grouping or null",
            "source_quotes": ["exact contiguous quote", "..."],
            "confidence": "HIGH|MEDIUM|LOW",
            "reason": "short explanation",
        },
    }


# Keys the model sometimes echoes back from the request. They carry no covenant
# semantics, so they are dropped before validation. Everything else still has to
# be a declared field — an invented semantic key remains a rejection.
_ECHOED_REQUEST_KEYS = frozenset({"scenario_id", "clause_id", "clause_text", "dsl"})


# A page number printed on its own line lands in the middle of an extracted
# sentence. Removing that one artifact class keeps grounding exact on the words.
_PAGE_NUMBER_LINE_RE = re.compile(r"(?m)^[ \t]*\d{1,4}[ \t]*$")


def _whitespace_normalized(text: str) -> str:
    """Collapse whitespace and drop page-number lines injected by extraction."""
    without_page_marks = _PAGE_NUMBER_LINE_RE.sub(" ", text.replace("\xa0", " "))
    return " ".join(without_page_marks.split())


def _quote_is_grounded(quote: str, clause_text: str) -> bool:
    """Exact contiguous grounding, compared modulo whitespace only.

    Characters and their order must match the source exactly. Nothing else is
    relaxed: this is not fuzzy matching, it only tolerates the irregular spacing
    that PDF text extraction introduces inside a sentence.
    """
    normalized_quote = _whitespace_normalized(quote)
    if not normalized_quote:
        return False
    return normalized_quote in _whitespace_normalized(clause_text)


def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Strip echoed request keys and unwrap a single-object envelope."""
    stripped = {key: value for key, value in payload.items() if key not in _ECHOED_REQUEST_KEYS}
    if "reported_actual" in stripped:
        return stripped
    nested = [value for value in stripped.values() if isinstance(value, dict)]
    if len(nested) == 1 and "reported_actual" in nested[0]:
        return {key: value for key, value in nested[0].items() if key not in _ECHOED_REQUEST_KEYS}
    return stripped


def _reject(scenario_id: str, clause_id: str, reason: str, *, called: bool) -> SemanticPlanResult:
    return SemanticPlanResult(
        plan=None,
        diagnostic={"scenario_id": scenario_id, "clause_id": clause_id, "reason": reason},
        model_called=called,
    )


def propose_plan(
    clause_text: str,
    *,
    scenario_id: str,
    clause_id: str,
    settings: Settings,
    gateway: SemanticJsonGateway | None = None,
) -> SemanticPlanResult:
    """Return a typed plan only when a HIGH-confidence, exactly-quoted candidate validates."""
    if not settings.semantic_fallback_enabled:
        return _reject(scenario_id, clause_id, "DISABLED", called=False)

    request = {
        "scenario_id": scenario_id,
        "clause_id": clause_id,
        "clause_text": clause_text,
        "dsl": _schema_hint(),
    }
    semantic_gateway = gateway or SemanticJsonGateway(settings=settings)
    response = semantic_gateway.propose(
        task_id=f"covenant-plan:{scenario_id}:{clause_id}",
        prompt_version="covenant-semantic-plan-v2",
        schema_version="covenant-plan-v2",
        source_sha256=sha256_text(clause_text),
        system_prompt=_SYSTEM,
        request_payload=request,
        max_tokens=2600,
    )
    if (
        response.state not in {SemanticJsonState.RESOLVED, SemanticJsonState.CACHE_HIT}
        or response.payload is None
    ):
        return SemanticPlanResult(
            plan=None,
            diagnostic={
                "scenario_id": scenario_id,
                "clause_id": clause_id,
                "reason": response.reason_code,
                "gateway_state": response.state.value,
            },
            model_called=response.model_called,
        )

    called = response.model_called
    try:
        candidate = _PlanCandidate.model_validate(_normalize_payload(response.payload))
    except ValidationError as exc:
        # Carry the failing locations: a rejected candidate that the model
        # understood correctly is a signal the DSL is missing something, and
        # that must stay visible rather than collapsing into a bare count.
        detail = ";".join(
            f"{'.'.join(str(part) for part in err['loc'])}:{err['type']}"
            for err in exc.errors()[:6]
        )
        return _reject(scenario_id, clause_id, f"CANDIDATE_SCHEMA_INVALID:{detail}", called=called)

    if candidate.confidence != "HIGH":
        # Keep the model's own explanation: a confident-but-inexpressible clause
        # is a DSL gap, and that must not disappear into a bare status code.
        return SemanticPlanResult(
            plan=None,
            diagnostic={
                "scenario_id": scenario_id,
                "clause_id": clause_id,
                "reason": "MODEL_NOT_HIGH_CONFIDENCE",
                "confidence": candidate.confidence,
                "model_reason": candidate.reason[:400],
            },
            model_called=called,
        )

    quotes = [q for q in candidate.source_quotes if q]
    if not quotes:
        return _reject(scenario_id, clause_id, "SOURCE_QUOTE_MISSING", called=called)
    ungrounded = [q for q in quotes if not _quote_is_grounded(q, clause_text)]
    if ungrounded:
        return _reject(
            scenario_id,
            clause_id,
            f"SOURCE_QUOTE_NOT_EXACT:{_whitespace_normalized(ungrounded[0])[:80]}",
            called=called,
        )

    try:
        reported_actual = EXPR_ADAPTER.validate_python(candidate.reported_actual)
        activation = BOOL_EXPR_ADAPTER.validate_python(candidate.activation_condition)
        breach = BOOL_EXPR_ADAPTER.validate_python(candidate.breach_condition)
    except ValidationError as exc:
        detail = ";".join(
            f"{'.'.join(str(part) for part in err['loc'][-3:])}:{err['type']}"
            for err in exc.errors()[:6]
        )
        return _reject(scenario_id, clause_id, f"AST_INVALID:{detail}", called=called)

    try:
        basis = PeriodBasis(candidate.period_basis)
        grouping = PeriodGrouping(candidate.period_grouping) if candidate.period_grouping else None
    except ValueError:
        return _reject(scenario_id, clause_id, "ENUM_NOT_ALLOWED", called=called)

    try:
        plan = finalize_plan(
            CovenantPlan(
                reported_actual=reported_actual,
                reported_actual_quantity_type=infer_quantity_type(reported_actual),
                activation_condition=activation,
                breach_condition=breach,
                period_basis=basis,
                period_grouping=grouping,
            )
        )
    except CovenantTypeError as exc:
        return _reject(scenario_id, clause_id, f"TYPE_ERROR:{exc.message}", called=called)
    except ValidationError as exc:
        return _reject(scenario_id, clause_id, f"PLAN_INVALID:{exc.error_count()}", called=called)

    return SemanticPlanResult(
        plan=plan,
        diagnostic={
            "scenario_id": scenario_id,
            "clause_id": clause_id,
            "status": "ACCEPTED",
            "quantity_type": plan.reported_actual_quantity_type.value,
            "required_facts": [f.category.value for f in plan.required_facts],
            "source_quotes": quotes,
            "reason": candidate.reason,
        },
        model_called=called,
    )
