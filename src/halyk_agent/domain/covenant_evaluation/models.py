"""Typed Stage 6 covenant-evaluation contracts.

The evaluator is intentionally small: Stage 5D supplies a typed semantic AST and
Stage 5F supplies row-level, calculation-ready inputs.  This package only plans,
validates and executes that already-trusted representation.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from halyk_agent.domain.common import CurrencyCode, NonEmptyStr
from halyk_agent.domain.covenants.ast import MetricCategory, TransactionSelector
from halyk_agent.domain.covenants.models import (
    Comparator,
    CovenantModifier,
    PeriodDefinition,
    ScopeDefinition,
)
from halyk_agent.domain.covenants.quantity import QuantityType, TypedQuantity
from halyk_agent.domain.transaction_taxonomy.models import (
    CalculationInput,
    DefinitionReadinessEntry,
    SelectorCoverageEntry,
)
from halyk_agent.domain.transactions import ExactDecimal

from .constants import (
    EVALUATION_ALGORITHM_VERSION,
    EVALUATION_PLAN_VERSION,
    EVALUATION_SCHEMA_VERSION,
)


class EvaluationNodeKind(StrEnum):
    SELECT = "SELECT"
    MATERIALITY_FILTER = "MATERIALITY_FILTER"
    CONSTANT = "CONSTANT"
    SUM = "SUM"
    COUNT = "COUNT"
    MIN = "MIN"
    MAX = "MAX"
    ADD = "ADD"
    SUBTRACT = "SUBTRACT"
    MULTIPLY = "MULTIPLY"
    DIVIDE = "DIVIDE"


class NodeResultStatus(StrEnum):
    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"
    ERROR = "ERROR"


class EvaluationStatus(StrEnum):
    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"
    ERROR = "ERROR"
    NOT_ACTIVATED = "NOT_ACTIVATED"


class ComplianceStatus(StrEnum):
    COMPLIANT = "COMPLIANT"
    BREACH = "BREACH"


class ActivationState(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    UNRESOLVED = "UNRESOLVED"
    ERROR = "ERROR"


class EvaluationIssueSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    UNRESOLVED = "UNRESOLVED"
    ERROR = "ERROR"


class EvaluationNumber(BaseModel):
    """Decimal-only scalar used inside the executor.

    MONEY normally requires a currency.  A currency-less zero is permitted only
    as an explicitly marked additive identity.  It is bound to a trusted
    covenant/input currency before publication whenever one is available.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    quantity_type: QuantityType
    value: ExactDecimal
    currency: CurrencyCode | None = None
    zero_currency_identity: bool = False

    @model_validator(mode="after")
    def _validate_currency(self) -> EvaluationNumber:
        if self.quantity_type is QuantityType.MONEY:
            if self.currency is None and not (self.zero_currency_identity and self.value == 0):
                raise ValueError(
                    "MONEY evaluation number requires currency unless it is an "
                    "explicit zero identity"
                )
        elif self.currency is not None:
            raise ValueError("non-money evaluation number must not carry currency")
        elif self.zero_currency_identity:
            raise ValueError("zero_currency_identity is only meaningful for MONEY")
        return self


class EvaluationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: NonEmptyStr
    severity: EvaluationIssueSeverity
    message: NonEmptyStr
    node_id: NonEmptyStr | None = None
    input_ids: tuple[NonEmptyStr, ...] = ()
    evidence_refs: tuple[NonEmptyStr, ...] = ()


class EvaluationNode(BaseModel):
    """One immutable node in the validated execution plan."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: NonEmptyStr
    kind: EvaluationNodeKind
    dependencies: tuple[NonEmptyStr, ...] = ()
    quantity_type: QuantityType | None = None
    selector: TransactionSelector | None = None
    constant: TypedQuantity | None = None
    materiality_threshold: TypedQuantity | None = None
    materiality_category: MetricCategory | None = None


class EvaluationPlan(BaseModel):
    """Execution plan produced deterministically from one CovenantDefinition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: NonEmptyStr = EVALUATION_SCHEMA_VERSION
    plan_version: NonEmptyStr = EVALUATION_PLAN_VERSION
    algorithm_version: NonEmptyStr = EVALUATION_ALGORITHM_VERSION

    plan_id: NonEmptyStr
    definition_id: NonEmptyStr
    scenario_id: NonEmptyStr
    clause_id: NonEmptyStr
    family_id: NonEmptyStr
    source_file: NonEmptyStr
    source_sha256: NonEmptyStr

    nodes: tuple[EvaluationNode, ...]
    root_node_id: NonEmptyStr
    metric_quantity_type: QuantityType
    comparator: Comparator
    threshold: TypedQuantity

    period: PeriodDefinition
    scope: ScopeDefinition
    modifiers: tuple[CovenantModifier, ...] = ()

    activation_root_node_id: NonEmptyStr | None = None
    activation_comparator: Comparator | None = None
    activation_threshold: TypedQuantity | None = None

    definition_evidence_span_ids: tuple[NonEmptyStr, ...] = ()


class EvaluationContext(BaseModel):
    """Stage 5F values bound for evaluation.

    Hash verification belongs to the app/I/O boundary; the domain validator still
    validates the typed content and ownership before execution.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    amount_contract_version: NonEmptyStr
    calculation_inputs: tuple[CalculationInput, ...]
    selector_coverage: tuple[SelectorCoverageEntry, ...]
    definition_readiness: tuple[DefinitionReadinessEntry, ...]


class EvaluationNodeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: NonEmptyStr
    status: NodeResultStatus
    selected_input_ids: tuple[NonEmptyStr, ...] = ()
    number: EvaluationNumber | None = None
    contributing_input_ids: tuple[NonEmptyStr, ...] = ()
    issues: tuple[EvaluationIssue, ...] = ()


class EvaluationTraceStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: int = Field(ge=0)
    node_id: NonEmptyStr
    kind: EvaluationNodeKind
    dependencies: tuple[NonEmptyStr, ...] = ()
    status: NodeResultStatus
    selected_input_ids: tuple[NonEmptyStr, ...] = ()
    contributing_input_ids: tuple[NonEmptyStr, ...] = ()
    quantity_type: QuantityType | None = None
    value: ExactDecimal | None = None
    currency: CurrencyCode | None = None
    issue_codes: tuple[NonEmptyStr, ...] = ()


class EvaluationTrace(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_id: NonEmptyStr
    execution_order: tuple[NonEmptyStr, ...]
    steps: tuple[EvaluationTraceStep, ...]
    issue_codes: tuple[NonEmptyStr, ...] = ()


class CovenantEvaluationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    definition_id: NonEmptyStr
    scenario_id: NonEmptyStr
    clause_id: NonEmptyStr
    family_id: NonEmptyStr
    status: EvaluationStatus
    compliance_status: ComplianceStatus | None = None
    activation_state: ActivationState = ActivationState.NOT_APPLICABLE
    actual: EvaluationNumber | None = None
    threshold: TypedQuantity
    contributing_input_ids: tuple[NonEmptyStr, ...] = ()
    contributing_transaction_ids: tuple[NonEmptyStr, ...] = ()
    issues: tuple[EvaluationIssue, ...] = ()
    trace: EvaluationTrace


class EvaluationManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: NonEmptyStr = EVALUATION_SCHEMA_VERSION
    algorithm_version: NonEmptyStr = EVALUATION_ALGORITHM_VERSION
    covenant_manifest_hash: NonEmptyStr
    taxonomy_manifest_hash: NonEmptyStr
    calculation_inputs_hash: NonEmptyStr
    selector_coverage_hash: NonEmptyStr
    definition_readiness_hash: NonEmptyStr
    plan_count: int = Field(ge=0)
    result_count: int = Field(ge=0)
    resolved_count: int = Field(ge=0)
    unresolved_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    not_activated_count: int = Field(ge=0)
    compliant_count: int = Field(ge=0)
    breach_count: int = Field(ge=0)
    plans_hash: NonEmptyStr
    results_hash: NonEmptyStr


class EvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest: EvaluationManifest
    plans: tuple[EvaluationPlan, ...]
    results: tuple[CovenantEvaluationResult, ...]
