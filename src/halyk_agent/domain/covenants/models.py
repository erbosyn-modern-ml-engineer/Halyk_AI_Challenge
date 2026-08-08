"""Covenant definition domain models (Stage 5D)."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from halyk_agent.domain.common import NonEmptyStr
from halyk_agent.domain.covenants.ast import (
    EXPR_ADAPTER,
    Expr,
    MetricCategory,
    TransactionSelector,
)
from halyk_agent.domain.covenants.constants import (
    COVENANT_COMPILER_VERSION,
    COVENANT_RULE_VERSION,
    COVENANT_SCHEMA_VERSION,
)
from halyk_agent.domain.covenants.quantity import QuantityType, TypedQuantity
from halyk_agent.domain.transactions import reject_float_amount


class Comparator(StrEnum):
    LT = "LT"
    LTE = "LTE"
    GT = "GT"
    GTE = "GTE"
    EQ = "EQ"


class PeriodKind(StrEnum):
    CLOSED_INTERVAL = "CLOSED_INTERVAL"
    AS_OF = "AS_OF"
    FINANCIAL_QUARTER = "FINANCIAL_QUARTER"
    MIXED = "MIXED"


class ScopeKind(StrEnum):
    BORROWER = "BORROWER"
    GROUP = "GROUP"
    BORROWER_AND_SUBSIDIARIES = "BORROWER_AND_SUBSIDIARIES"
    RELATED_PARTY_SET = "RELATED_PARTY_SET"
    EXPRESSION_SCOPED = "EXPRESSION_SCOPED"


class ScopeProvenance(StrEnum):
    EXPLICIT_TEXT = "EXPLICIT_TEXT"
    SELECTOR_DERIVED = "SELECTOR_DERIVED"
    DEFAULT_BORROWER_BY_RULE = "DEFAULT_BORROWER_BY_RULE"
    EXPRESSION_OPERAND_SCOPES = "EXPRESSION_OPERAND_SCOPES"


class CompileStatus(StrEnum):
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED_FORMULA = "UNSUPPORTED_FORMULA"
    AMBIGUOUS_OPERATOR = "AMBIGUOUS_OPERATOR"
    AMBIGUOUS_THRESHOLD = "AMBIGUOUS_THRESHOLD"
    MALFORMED_THRESHOLD = "MALFORMED_THRESHOLD"
    UNRESOLVED_PERIOD = "UNRESOLVED_PERIOD"
    UNRESOLVED_SCOPE = "UNRESOLVED_SCOPE"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    TYPE_ERROR = "TYPE_ERROR"
    MISSING_AUTHORITY = "MISSING_AUTHORITY"
    MISSING_CLAUSE = "MISSING_CLAUSE"


class BoundaryInclusivity(StrEnum):
    INCLUSIVE = "INCLUSIVE"
    EXCLUSIVE = "EXCLUSIVE"


class CovenantModifierKind(StrEnum):
    """Covenant-side semantic instructions retained for later stages."""

    AUDITOR_RECLASSIFICATION_INCLUDE = "AUDITOR_RECLASSIFICATION_INCLUDE"
    AUDITOR_RECLASSIFICATION_EXCLUDE = "AUDITOR_RECLASSIFICATION_EXCLUDE"
    REJECTED_RECLASSIFICATION_EXCLUDE = "REJECTED_RECLASSIFICATION_EXCLUDE"
    MATERIALITY_FLOOR = "MATERIALITY_FLOOR"
    BOTH_NUMERATOR_AND_DENOMINATOR_RECLASS = "BOTH_NUMERATOR_AND_DENOMINATOR_RECLASS"


class PeriodDefinition(BaseModel):
    """Explicit measurement period with inclusive/exclusive bounds."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    period_kind: PeriodKind
    start_date: date | None = None
    end_date: date | None = None
    as_of_date: date | None = None
    start_inclusive: BoundaryInclusivity = BoundaryInclusivity.INCLUSIVE
    end_inclusive: BoundaryInclusivity = BoundaryInclusivity.INCLUSIVE
    quarter: int | None = Field(default=None, ge=1, le=4)
    evidence_span_ids: tuple[NonEmptyStr, ...] = ()
    # Optional secondary flow period when AS_OF coexists with a closed interval.
    flow_start_date: date | None = None
    flow_end_date: date | None = None


class ScopeDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_kind: ScopeKind
    provenance: ScopeProvenance = ScopeProvenance.DEFAULT_BORROWER_BY_RULE
    evidence_span_ids: tuple[NonEmptyStr, ...] = ()
    matched_text: NonEmptyStr | None = None


class ActivationCondition(BaseModel):
    """Optional springing / conditional activation predicate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metric: Expr
    comparator: Comparator
    threshold: TypedQuantity
    evidence_span_ids: tuple[NonEmptyStr, ...] = ()


class CovenantModifier(BaseModel):
    """Covenant instruction that later stages must satisfy (not ledger facts)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: CovenantModifierKind
    detail: NonEmptyStr
    evidence_span_ids: tuple[NonEmptyStr, ...] = ()
    # Quantitative modifiers (e.g. MATERIALITY_FLOOR) carry a typed threshold.
    # Non-quantitative modifiers leave threshold / applies_to_category as None.
    threshold: TypedQuantity | None = None
    applies_to_category: MetricCategory | None = None

    @field_validator("threshold", mode="before")
    @classmethod
    def _threshold_no_float(cls, value: Any) -> Any:
        if isinstance(value, dict) and "value" in value:
            reject_float_amount(value["value"])
        return value


class CovenantEvidenceRefs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    clause_span_ids: tuple[NonEmptyStr, ...] = ()
    formula_span_ids: tuple[NonEmptyStr, ...] = ()
    comparator_span_ids: tuple[NonEmptyStr, ...] = ()
    threshold_span_ids: tuple[NonEmptyStr, ...] = ()
    period_span_ids: tuple[NonEmptyStr, ...] = ()
    scope_span_ids: tuple[NonEmptyStr, ...] = ()
    activation_span_ids: tuple[NonEmptyStr, ...] = ()
    modifier_span_ids: tuple[NonEmptyStr, ...] = ()


class CovenantDefinition(BaseModel):
    """Compiled typed covenant cell definition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    definition_id: NonEmptyStr
    scenario_id: NonEmptyStr
    clause_id: NonEmptyStr
    document_id: NonEmptyStr
    document_version_id: NonEmptyStr
    source_file: NonEmptyStr
    source_sha256: NonEmptyStr
    family_id: NonEmptyStr
    metric: Expr
    metric_quantity_type: QuantityType
    comparator: Comparator
    threshold: TypedQuantity
    period: PeriodDefinition
    scope: ScopeDefinition
    selectors: tuple[TransactionSelector, ...] = ()
    modifiers: tuple[CovenantModifier, ...] = ()
    activation_condition: ActivationCondition | None = None
    evidence: CovenantEvidenceRefs
    rendered: NonEmptyStr
    rule_version: NonEmptyStr = COVENANT_RULE_VERSION
    compiler_version: NonEmptyStr = COVENANT_COMPILER_VERSION
    schema_version: NonEmptyStr = COVENANT_SCHEMA_VERSION

    @field_validator("threshold", mode="before")
    @classmethod
    def _threshold_no_float(cls, value: Any) -> Any:
        if isinstance(value, dict) and "value" in value:
            reject_float_amount(value["value"])
        return value


class CovenantCompileFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    failure_id: NonEmptyStr
    scenario_id: NonEmptyStr
    clause_id: NonEmptyStr
    status: CompileStatus
    reason: NonEmptyStr
    document_id: NonEmptyStr | None = None
    evidence_span_ids: tuple[NonEmptyStr, ...] = ()


class CovenantManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: NonEmptyStr = COVENANT_SCHEMA_VERSION
    compiler_version: NonEmptyStr = COVENANT_COMPILER_VERSION
    rule_version: NonEmptyStr = COVENANT_RULE_VERSION
    authority_manifest_hash: NonEmptyStr
    template_answers_hash: NonEmptyStr
    canonical_documents_hash: NonEmptyStr
    scenario_count: int = Field(ge=0)
    cell_count: int = Field(ge=0)
    authoritative_covenant_docs: int = Field(ge=0)
    definition_count: int = Field(ge=0)
    supported_count: int = Field(ge=0)
    unsupported_count: int = Field(ge=0)
    failure_count: int = Field(ge=0)
    evidence_span_count: int = Field(ge=0)
    evidence_hash: NonEmptyStr
    definitions_hash: NonEmptyStr


class CovenantReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest: CovenantManifest
    definitions: tuple[CovenantDefinition, ...]
    failures: tuple[CovenantCompileFailure, ...]
    spans: tuple[Any, ...] = ()


def parse_expr(payload: dict[str, Any] | Expr) -> Expr:
    if isinstance(payload, BaseModel):
        return payload
    return EXPR_ADAPTER.validate_python(payload)
