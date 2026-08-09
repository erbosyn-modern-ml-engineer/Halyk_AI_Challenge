"""Typed models for Stage 5F transaction taxonomy and calculation inputs."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from halyk_agent.domain.common import NonEmptyStr
from halyk_agent.domain.covenants.ast import MetricCategory
from halyk_agent.domain.transaction_taxonomy.constants import (
    TAXONOMY_ALGORITHM_VERSION,
    TAXONOMY_CLASSIFIER_VERSION,
    TAXONOMY_SCHEMA_VERSION,
)
from halyk_agent.domain.transactions import ExactDecimal, reject_float_amount


class ClassificationStatus(StrEnum):
    CLASSIFIED = "CLASSIFIED"
    UNRESOLVED = "UNRESOLVED"
    CONFLICT = "CONFLICT"
    IRRELEVANT = "IRRELEVANT"
    ROUTING_NOISE = "ROUTING_NOISE"


class ClassificationMethod(StrEnum):
    LEDGER_DESCRIPTION_RULE = "LEDGER_DESCRIPTION_RULE"
    AUTHORITATIVE_RECLASSIFICATION = "AUTHORITATIVE_RECLASSIFICATION"
    SEMANTIC_FALLBACK = "SEMANTIC_FALLBACK"
    UNRESOLVED = "UNRESOLVED"
    CONFLICT = "CONFLICT"
    ROUTING_NOISE = "ROUTING_NOISE"


class RelatedPartyStatus(StrEnum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"


class RelatedPartyBasis(StrEnum):
    OWNERSHIP_THRESHOLD = "OWNERSHIP_THRESHOLD"
    BELOW_THRESHOLD = "BELOW_THRESHOLD"
    NO_IDENTITY_MATCH = "NO_IDENTITY_MATCH"
    AMBIGUOUS_IDENTITY = "AMBIGUOUS_IDENTITY"
    MISSING_THRESHOLD = "MISSING_THRESHOLD"
    MISSING_OWNERSHIP = "MISSING_OWNERSHIP"
    ROUTING_NOISE = "ROUTING_NOISE"
    DAMAGED_OWNERSHIP_IDENTITY = "DAMAGED_OWNERSHIP_IDENTITY"


class SubsidiaryStatusKind(StrEnum):
    UNRESTRICTED = "UNRESTRICTED"
    RESTRICTED = "RESTRICTED"
    GROUP_MEMBER = "GROUP_MEMBER"
    UNKNOWN = "UNKNOWN"


class SelectorReadinessStatus(StrEnum):
    READY = "READY"
    TRUE_ZERO = "TRUE_ZERO"
    UNRESOLVED = "UNRESOLVED"


class EntityScopeKind(StrEnum):
    BORROWER = "BORROWER"
    GROUP = "GROUP"
    SUBSIDIARY = "SUBSIDIARY"
    RELATED_PARTY = "RELATED_PARTY"
    UNKNOWN = "UNKNOWN"


class AdjustmentEventType(StrEnum):
    CATEGORY_RECLASSIFICATION_ACCEPTED = "CATEGORY_RECLASSIFICATION_ACCEPTED"
    CATEGORY_RECLASSIFICATION_REJECTED = "CATEGORY_RECLASSIFICATION_REJECTED"
    AMOUNT_CORRECTION = "AMOUNT_CORRECTION"
    PERIOD_ASSIGNMENT = "PERIOD_ASSIGNMENT"
    PERIOD_EXCLUSION = "PERIOD_EXCLUSION"
    OFF_LEDGER_INPUT = "OFF_LEDGER_INPUT"
    FX_SETTLEMENT_REFERENCE = "FX_SETTLEMENT_REFERENCE"
    TRANSACTION_TREATMENT_INCLUDE = "TRANSACTION_TREATMENT_INCLUDE"
    TRANSACTION_TREATMENT_EXCLUDE = "TRANSACTION_TREATMENT_EXCLUDE"


class UnresolvedReason(StrEnum):
    CATEGORY_UNKNOWN = "CATEGORY_UNKNOWN"
    CATEGORY_CONFLICT = "CATEGORY_CONFLICT"
    COUNTERPARTY_IDENTITY_AMBIGUOUS = "COUNTERPARTY_IDENTITY_AMBIGUOUS"
    RELATED_PARTY_UNKNOWN = "RELATED_PARTY_UNKNOWN"
    PERIOD_UNKNOWN = "PERIOD_UNKNOWN"
    FACT_CONFLICT = "FACT_CONFLICT"
    AMOUNT_MISSING = "AMOUNT_MISSING"
    AMOUNT_INVALID = "AMOUNT_INVALID"
    IRRELEVANT_TO_COMPILED_COVENANTS = "IRRELEVANT_TO_COMPILED_COVENANTS"
    ROUTING_UNRESOLVED = "ROUTING_UNRESOLVED"
    FACT_LINK_AMBIGUOUS = "FACT_LINK_AMBIGUOUS"
    FACT_TXN_SCENARIO_MISMATCH = "FACT_TXN_SCENARIO_MISMATCH"
    SIGN_DIRECTION_UNRESOLVED = "SIGN_DIRECTION_UNRESOLVED"


class ConflictKind(StrEnum):
    CATEGORY_COLLISION = "CATEGORY_COLLISION"
    ACCEPTED_RECLASS_CONFLICT = "ACCEPTED_RECLASS_CONFLICT"
    AMOUNT_CORRECTION_CONFLICT = "AMOUNT_CORRECTION_CONFLICT"
    PERIOD_CONFLICT = "PERIOD_CONFLICT"
    ENTITY_IDENTITY_AMBIGUOUS = "ENTITY_IDENTITY_AMBIGUOUS"
    FACT_LINK_AMBIGUOUS = "FACT_LINK_AMBIGUOUS"
    FACT_SCENARIO_MISMATCH = "FACT_SCENARIO_MISMATCH"
    SUBSIDIARY_STATUS_CONFLICT = "SUBSIDIARY_STATUS_CONFLICT"
    TREATMENT_CONFLICT = "TREATMENT_CONFLICT"


class InputSourceKind(StrEnum):
    LEDGER_ROW = "LEDGER_ROW"
    AUTHORITATIVE_FACT = "AUTHORITATIVE_FACT"


class AmountSemantics(StrEnum):
    SOURCE_SIGNED_FLOW = "SOURCE_SIGNED_FLOW"
    POSITIVE_MAGNITUDE = "POSITIVE_MAGNITUDE"


class SignRule(StrEnum):
    INFLOW_AS_IS = "INFLOW_AS_IS"
    EXPENSE_NEGATE_SOURCE = "EXPENSE_NEGATE_SOURCE"
    POSITIVE_MAGNITUDE_AS_IS = "POSITIVE_MAGNITUDE_AS_IS"


AMOUNT_CONTRACT_VERSION = "halyk.metric_amount.v1"


class PeriodMembershipHint(StrEnum):
    """Hints for Stage 6 period predicates — not covenant evaluation."""

    IN_LEDGER_DATE = "IN_LEDGER_DATE"
    ASSIGNED_SERVICE_PERIOD = "ASSIGNED_SERVICE_PERIOD"
    EXCLUDED_FROM_PERIOD = "EXCLUDED_FROM_PERIOD"
    UNKNOWN = "UNKNOWN"


class InputPeriodSemantics(StrEnum):
    """
    Whether a CalculationInput participates as a FLOW-period quantity or an AS_OF stock.

    Derived from upstream fact/requirement/ledger semantics — never from source_kind alone.
    """

    FLOW = "FLOW"
    AS_OF = "AS_OF"


class MoneyValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    value: ExactDecimal
    currency: NonEmptyStr

    @field_validator("value", mode="before")
    @classmethod
    def _no_float(cls, value: Any) -> Any:
        return reject_float_amount(value)


class AdjustmentEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: NonEmptyStr
    event_type: AdjustmentEventType
    scenario_id: NonEmptyStr
    fact_id: NonEmptyStr
    transaction_id: NonEmptyStr | None = None
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)
    evidence_span_ids: tuple[NonEmptyStr, ...] = ()
    authority_domain: NonEmptyStr | None = None
    reason_code: NonEmptyStr


class ClassifiedTransaction(BaseModel):
    """Canonical ledger row after classification and authoritative adjustments."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    transaction_id: NonEmptyStr
    source_ledger: NonEmptyStr
    source_row_index: int = Field(ge=0)
    source_sha256: NonEmptyStr
    scenario_id: NonEmptyStr | None = None
    account_id: NonEmptyStr

    original_amount: ExactDecimal | None = None
    original_currency: NonEmptyStr
    effective_amount: ExactDecimal | None = None
    effective_currency: NonEmptyStr

    original_category: MetricCategory | None = None
    effective_category: MetricCategory | None = None

    original_date: date | None = None
    effective_period_start: date | None = None
    effective_period_end: date | None = None
    period_membership: PeriodMembershipHint = PeriodMembershipHint.IN_LEDGER_DATE
    period_excluded: bool = False

    counterparty_raw: NonEmptyStr
    counterparty_identity_key: NonEmptyStr | None = None
    description: NonEmptyStr

    related_party_status: RelatedPartyStatus = RelatedPartyStatus.UNKNOWN
    related_party_basis: RelatedPartyBasis = RelatedPartyBasis.MISSING_OWNERSHIP
    entity_scope: EntityScopeKind = EntityScopeKind.BORROWER
    subsidiary_status: SubsidiaryStatusKind = SubsidiaryStatusKind.UNKNOWN
    selector_categories: tuple[MetricCategory, ...] = ()
    membership_reasons: tuple[NonEmptyStr, ...] = ()

    classification_status: ClassificationStatus
    classification_method: ClassificationMethod
    classification_rule: NonEmptyStr | None = None

    applied_fact_ids: tuple[NonEmptyStr, ...] = ()
    rejected_fact_ids: tuple[NonEmptyStr, ...] = ()
    evidence_refs: tuple[NonEmptyStr, ...] = ()
    unresolved_reasons: tuple[UnresolvedReason, ...] = ()
    conflict_ids: tuple[NonEmptyStr, ...] = ()

    @field_validator("original_amount", "effective_amount", mode="before")
    @classmethod
    def _no_float_amt(cls, value: Any) -> Any:
        if value is None or value == "":
            return None
        return reject_float_amount(value)


class DerivedCalculationInput(BaseModel):
    """Authoritative off-ledger / fact-derived input (never a fake ledger row)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_id: NonEmptyStr
    scenario_id: NonEmptyStr
    source_kind: Literal[InputSourceKind.AUTHORITATIVE_FACT] = InputSourceKind.AUTHORITATIVE_FACT
    fact_id: NonEmptyStr
    fact_kind: NonEmptyStr
    category: MetricCategory
    amount: ExactDecimal
    currency: NonEmptyStr
    period_semantics: InputPeriodSemantics
    as_of_date: date | None = None
    period_start: date | None = None
    period_end: date | None = None
    label: NonEmptyStr | None = None
    related_party_status: RelatedPartyStatus = RelatedPartyStatus.UNKNOWN
    entity_scope: EntityScopeKind = EntityScopeKind.BORROWER
    evidence_span_ids: tuple[NonEmptyStr, ...] = ()

    @field_validator("amount", mode="before")
    @classmethod
    def _no_float(cls, value: Any) -> Any:
        return reject_float_amount(value)


class CalculationInput(BaseModel):
    """Row-level Stage 6 consumption unit — no aggregation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_id: NonEmptyStr
    scenario_id: NonEmptyStr
    source_kind: InputSourceKind
    transaction_id: NonEmptyStr | None = None
    derived_input_id: NonEmptyStr | None = None
    category: MetricCategory
    selector_categories: tuple[MetricCategory, ...] = ()
    membership_reasons: tuple[NonEmptyStr, ...] = ()
    # Stage 6 aggregates ``amount`` (== metric_amount). ``source_amount`` is audit-only.
    amount: ExactDecimal
    source_amount: ExactDecimal | None = None
    metric_amount: ExactDecimal | None = None
    amount_semantics: AmountSemantics = AmountSemantics.SOURCE_SIGNED_FLOW
    sign_rule: SignRule = SignRule.EXPENSE_NEGATE_SOURCE
    currency: NonEmptyStr
    period_semantics: InputPeriodSemantics
    transaction_date: date | None = None
    period_start: date | None = None
    period_end: date | None = None
    as_of_date: date | None = None
    period_excluded: bool = False
    counterparty: NonEmptyStr | None = None
    related_party: RelatedPartyStatus
    entity_scope: EntityScopeKind
    subsidiary_status: SubsidiaryStatusKind = SubsidiaryStatusKind.UNKNOWN
    flags: tuple[NonEmptyStr, ...] = ()
    applied_fact_ids: tuple[NonEmptyStr, ...] = ()
    rejected_fact_ids: tuple[NonEmptyStr, ...] = ()
    provenance_refs: tuple[NonEmptyStr, ...] = ()
    classification_rule: NonEmptyStr | None = None

    @field_validator("amount", "source_amount", "metric_amount", mode="before")
    @classmethod
    def _no_float(cls, value: Any) -> Any:
        if value is None or value == "":
            return None
        return reject_float_amount(value)


class TaxonomyConflict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    conflict_id: NonEmptyStr
    kind: ConflictKind
    scenario_id: NonEmptyStr | None = None
    transaction_id: NonEmptyStr | None = None
    fact_ids: tuple[NonEmptyStr, ...] = ()
    reason: NonEmptyStr
    details: dict[str, Any] = Field(default_factory=dict)


class UnresolvedTransaction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    transaction_id: NonEmptyStr
    scenario_id: NonEmptyStr | None
    reasons: tuple[UnresolvedReason, ...]
    potentially_relevant: bool
    description: NonEmptyStr
    notes: NonEmptyStr | None = None


class SelectorCoverageEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    definition_id: NonEmptyStr
    scenario_id: NonEmptyStr
    category: MetricCategory
    related_party_only: bool
    group_level: bool
    include_flags: tuple[NonEmptyStr, ...] = ()
    exclude_flags: tuple[NonEmptyStr, ...] = ()
    status: SelectorReadinessStatus
    reason_code: NonEmptyStr = "OK"
    matching_input_count: int = Field(ge=0, default=0)


class DefinitionReadinessEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    definition_id: NonEmptyStr
    scenario_id: NonEmptyStr
    status: SelectorReadinessStatus
    reason_code: NonEmptyStr
    unresolved_selectors: tuple[NonEmptyStr, ...] = ()


class FactConsumptionEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fact_id: NonEmptyStr
    fact_kind: NonEmptyStr
    scenario_id: NonEmptyStr
    disposition: Literal["CONSUMED", "DEFERRED_STAGE_6", "UNUSED"]
    explanation: NonEmptyStr


class TaxonomyManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: NonEmptyStr = TAXONOMY_SCHEMA_VERSION
    algorithm_version: NonEmptyStr = TAXONOMY_ALGORITHM_VERSION
    classifier_version: NonEmptyStr = TAXONOMY_CLASSIFIER_VERSION
    routing_manifest_hash: NonEmptyStr
    covenant_manifest_hash: NonEmptyStr
    facts_manifest_hash: NonEmptyStr
    ledger_source_sha256: NonEmptyStr
    scenario_count: int = Field(ge=0)
    ledger_row_count: int = Field(ge=0)
    scenario_linked_count: int = Field(ge=0)
    routing_noise_count: int = Field(ge=0)
    classified_count: int = Field(ge=0)
    unresolved_count: int = Field(ge=0)
    conflict_count: int = Field(ge=0)
    irrelevant_count: int = Field(ge=0)
    calculation_input_count: int = Field(ge=0)
    derived_input_count: int = Field(ge=0)
    adjustment_event_count: int = Field(ge=0)
    selector_count: int = Field(ge=0)
    selector_ready_count: int = Field(ge=0)
    selector_true_zero_count: int = Field(ge=0)
    selector_unresolved_count: int = Field(ge=0)
    # Deprecated aliases retained for summary compatibility.
    selector_supported_count: int = Field(ge=0)
    selector_unsupported_count: int = Field(ge=0)
    definition_ready_count: int = Field(ge=0)
    definition_unresolved_count: int = Field(ge=0)
    amount_contract_version: NonEmptyStr = AMOUNT_CONTRACT_VERSION
    accepted_facts_count: int = Field(ge=0)
    facts_consumed_count: int = Field(ge=0)
    related_party_true_count: int = Field(ge=0)
    related_party_false_count: int = Field(ge=0)
    related_party_unknown_count: int = Field(ge=0)
    semantic_model_calls: int = Field(ge=0, default=0)
    semantic_fallback_count: int = Field(ge=0, default=0)
    category_counts: dict[str, int] = Field(default_factory=dict)
    membership_counts: dict[str, int] = Field(default_factory=dict)
    method_counts: dict[str, int] = Field(default_factory=dict)
    taxonomy_hash: NonEmptyStr
    calculation_inputs_hash: NonEmptyStr
    adjustments_hash: NonEmptyStr
    selector_coverage_hash: NonEmptyStr
    definition_readiness_hash: NonEmptyStr


class TaxonomyReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest: TaxonomyManifest
    classified: tuple[ClassifiedTransaction, ...]
    adjustments: tuple[AdjustmentEvent, ...]
    calculation_inputs: tuple[CalculationInput, ...]
    derived_inputs: tuple[DerivedCalculationInput, ...]
    conflicts: tuple[TaxonomyConflict, ...]
    unresolved: tuple[UnresolvedTransaction, ...]
    selector_coverage: tuple[SelectorCoverageEntry, ...]
    definition_readiness: tuple[DefinitionReadinessEntry, ...] = ()
    fact_consumption: tuple[FactConsumptionEntry, ...] = ()
    qualifying_related_parties: tuple[dict[str, Any], ...] = ()
