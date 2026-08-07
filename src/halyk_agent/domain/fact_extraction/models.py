"""Typed Stage 5E fact requirements, records, and payloads."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from halyk_agent.domain.authority.models import AuthorityDomain
from halyk_agent.domain.common import NonEmptyStr
from halyk_agent.domain.evidence import EvidenceSpan
from halyk_agent.domain.fact_extraction.constants import (
    FACT_EXTRACTOR_VERSION,
    FACT_SCHEMA_VERSION,
    FACT_VALIDATOR_VERSION,
)
from halyk_agent.domain.models_gateway.types import ModelCallRecord
from halyk_agent.domain.transactions import ExactDecimal, reject_float_amount


class FactKind(StrEnum):
    TRANSACTION_RECLASSIFICATION = "TRANSACTION_RECLASSIFICATION"
    TRANSACTION_PERIOD = "TRANSACTION_PERIOD"
    AMOUNT_CORRECTION = "AMOUNT_CORRECTION"
    OFF_LEDGER_AMOUNT = "OFF_LEDGER_AMOUNT"
    OWNERSHIP = "OWNERSHIP"
    RELATED_PARTY_THRESHOLD = "RELATED_PARTY_THRESHOLD"
    SUBSIDIARY_STATUS = "SUBSIDIARY_STATUS"
    FX_RATE = "FX_RATE"
    ONE_TIME_ADD_BACK = "ONE_TIME_ADD_BACK"
    TRANSACTION_TREATMENT = "TRANSACTION_TREATMENT"


class ExtractionMethod(StrEnum):
    DETERMINISTIC = "DETERMINISTIC"
    LLM_PRIMARY = "LLM_PRIMARY"
    LLM_ESCALATION = "LLM_ESCALATION"
    MERGED = "MERGED"


class FactValidatorStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED_EVIDENCE = "REJECTED_EVIDENCE"
    REJECTED_SCHEMA = "REJECTED_SCHEMA"
    REJECTED_SEMANTIC = "REJECTED_SEMANTIC"
    AMBIGUOUS = "AMBIGUOUS"
    UNRESOLVED = "UNRESOLVED"
    CONFLICT = "CONFLICT"


class ReclassificationDisposition(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class PeriodDisposition(StrEnum):
    EXCLUDE_FROM_PERIOD = "EXCLUDE_FROM_PERIOD"
    ASSIGN_TO_PERIOD = "ASSIGN_TO_PERIOD"


class TreatmentDisposition(StrEnum):
    INCLUDE = "INCLUDE"
    EXCLUDE = "EXCLUDE"


class SubsidiaryKind(StrEnum):
    RESTRICTED = "RESTRICTED"
    UNRESTRICTED = "UNRESTRICTED"
    GROUP_MEMBER = "GROUP_MEMBER"


class MoneyAmount(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    value: ExactDecimal
    currency: NonEmptyStr

    @field_validator("value", mode="before")
    @classmethod
    def _no_float(cls, value: Any) -> Any:
        return reject_float_amount(value)


class TransactionReclassificationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[FactKind.TRANSACTION_RECLASSIFICATION] = FactKind.TRANSACTION_RECLASSIFICATION
    transaction_id: NonEmptyStr | None = None
    counterparty: NonEmptyStr | None = None
    amount: MoneyAmount | None = None
    from_category: NonEmptyStr
    to_category: NonEmptyStr
    disposition: ReclassificationDisposition = ReclassificationDisposition.ACCEPTED


class TransactionPeriodPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[FactKind.TRANSACTION_PERIOD] = FactKind.TRANSACTION_PERIOD
    transaction_id: NonEmptyStr
    disposition: PeriodDisposition
    period_label: NonEmptyStr | None = None
    service_start: date | None = None
    service_end: date | None = None


class AmountCorrectionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[FactKind.AMOUNT_CORRECTION] = FactKind.AMOUNT_CORRECTION
    transaction_id: NonEmptyStr | None = None
    amount: MoneyAmount
    description: NonEmptyStr | None = None


class OffLedgerAmountPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[FactKind.OFF_LEDGER_AMOUNT] = FactKind.OFF_LEDGER_AMOUNT
    label: NonEmptyStr
    amount: MoneyAmount
    as_of_date: date | None = None


class OwnershipPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[FactKind.OWNERSHIP] = FactKind.OWNERSHIP
    entity_name: NonEmptyStr
    ownership_percent: ExactDecimal
    holder_label: NonEmptyStr = "GROUP"
    voting_rights: bool = True

    @field_validator("ownership_percent", mode="before")
    @classmethod
    def _no_float(cls, value: Any) -> Any:
        return reject_float_amount(value)

    @model_validator(mode="after")
    def _bounds(self) -> OwnershipPayload:
        if self.ownership_percent < 0 or self.ownership_percent > Decimal("100"):
            raise ValueError("ownership_percent must be between 0 and 100")
        return self


class RelatedPartyThresholdPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[FactKind.RELATED_PARTY_THRESHOLD] = FactKind.RELATED_PARTY_THRESHOLD
    threshold_percent: ExactDecimal
    holder_label: NonEmptyStr = "GROUP"

    @field_validator("threshold_percent", mode="before")
    @classmethod
    def _no_float(cls, value: Any) -> Any:
        return reject_float_amount(value)

    @model_validator(mode="after")
    def _bounds(self) -> RelatedPartyThresholdPayload:
        if self.threshold_percent < 0 or self.threshold_percent > Decimal("100"):
            raise ValueError("threshold_percent must be between 0 and 100")
        return self


class SubsidiaryStatusPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[FactKind.SUBSIDIARY_STATUS] = FactKind.SUBSIDIARY_STATUS
    entity_name: NonEmptyStr
    status: SubsidiaryKind


class FxRatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[FactKind.FX_RATE] = FactKind.FX_RATE
    from_currency: NonEmptyStr
    to_currency: NonEmptyStr
    rate: ExactDecimal
    as_of_date: date | None = None
    transaction_id: NonEmptyStr | None = None

    @field_validator("rate", mode="before")
    @classmethod
    def _no_float(cls, value: Any) -> Any:
        return reject_float_amount(value)

    @model_validator(mode="after")
    def _positive(self) -> FxRatePayload:
        if self.rate <= 0:
            raise ValueError("FX rate must be positive")
        return self


class OneTimeAddBackPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[FactKind.ONE_TIME_ADD_BACK] = FactKind.ONE_TIME_ADD_BACK
    label: NonEmptyStr
    amount: MoneyAmount
    materiality_note: NonEmptyStr | None = None


class TransactionTreatmentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[FactKind.TRANSACTION_TREATMENT] = FactKind.TRANSACTION_TREATMENT
    transaction_id: NonEmptyStr
    disposition: TreatmentDisposition
    reason: NonEmptyStr | None = None


FactPayload = Annotated[
    TransactionReclassificationPayload
    | TransactionPeriodPayload
    | AmountCorrectionPayload
    | OffLedgerAmountPayload
    | OwnershipPayload
    | RelatedPartyThresholdPayload
    | SubsidiaryStatusPayload
    | FxRatePayload
    | OneTimeAddBackPayload
    | TransactionTreatmentPayload,
    Field(discriminator="kind"),
]


class FactRequirement(BaseModel):
    """Demand-driven extraction request derived from Stage 5D semantics."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement_id: NonEmptyStr
    scenario_id: NonEmptyStr
    fact_kind: FactKind
    authority_domain: AuthorityDomain
    clause_ids: tuple[NonEmptyStr, ...] = ()
    modifier_kinds: tuple[NonEmptyStr, ...] = ()
    selector_categories: tuple[NonEmptyStr, ...] = ()
    reason_code: NonEmptyStr
    lexical_cues: tuple[NonEmptyStr, ...] = ()


class ModelProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: NonEmptyStr
    model: NonEmptyStr
    prompt_version: NonEmptyStr
    schema_version: NonEmptyStr
    request_hash: NonEmptyStr
    call_id: NonEmptyStr | None = None
    attempt: int = Field(default=1, ge=1)
    latency_ms: int | None = Field(default=None, ge=0)
    confidence: ExactDecimal | None = None


class FactRecord(BaseModel):
    """Trusted or rejected Stage 5E fact assertion (never mutates ledger)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fact_id: NonEmptyStr
    scenario_id: NonEmptyStr
    fact_kind: FactKind
    payload: FactPayload
    authority_domain: AuthorityDomain
    source_document_id: NonEmptyStr
    source_file: NonEmptyStr
    source_sha256: NonEmptyStr
    evidence_span_ids: tuple[NonEmptyStr, ...] = ()
    extraction_method: ExtractionMethod
    validator_status: FactValidatorStatus
    requirement_ids: tuple[NonEmptyStr, ...] = ()
    reason_code: NonEmptyStr
    warnings: tuple[NonEmptyStr, ...] = ()
    model_provenance: ModelProvenance | None = None
    schema_version: NonEmptyStr = FACT_SCHEMA_VERSION
    extractor_version: NonEmptyStr = FACT_EXTRACTOR_VERSION
    validator_version: NonEmptyStr = FACT_VALIDATOR_VERSION


class FactCandidate(BaseModel):
    """Pre-validation extraction candidate (deterministic or LLM)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: NonEmptyStr
    requirement_id: NonEmptyStr | None
    scenario_id: NonEmptyStr
    fact_kind: FactKind
    payload: FactPayload
    authority_domain: AuthorityDomain
    source_document_id: NonEmptyStr
    source_file: NonEmptyStr
    source_sha256: NonEmptyStr
    extraction_method: ExtractionMethod
    reason_code: NonEmptyStr
    quote: NonEmptyStr
    page_number: int = Field(ge=1)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    fragment_ids: tuple[NonEmptyStr, ...] = ()
    model_provenance: ModelProvenance | None = None


class FactConflict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    conflict_id: NonEmptyStr
    scenario_id: NonEmptyStr
    fact_kind: FactKind
    fact_ids: tuple[NonEmptyStr, ...]
    reason: NonEmptyStr


class FactExtractionManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: NonEmptyStr = FACT_SCHEMA_VERSION
    extractor_version: NonEmptyStr = FACT_EXTRACTOR_VERSION
    validator_version: NonEmptyStr = FACT_VALIDATOR_VERSION
    authority_manifest_hash: NonEmptyStr
    covenant_definitions_hash: NonEmptyStr
    canonical_documents_hash: NonEmptyStr
    scenario_count: int = Field(ge=0)
    requirement_count: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    accepted_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    unresolved_count: int = Field(ge=0)
    conflict_count: int = Field(ge=0)
    model_call_count: int = Field(ge=0)
    deterministic_accepted_count: int = Field(ge=0)
    llm_accepted_count: int = Field(ge=0)
    evidence_span_count: int = Field(ge=0)
    allow_network_models: bool = False
    requirements_hash: NonEmptyStr
    accepted_facts_hash: NonEmptyStr
    evidence_hash: NonEmptyStr


class FactExtractionReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest: FactExtractionManifest
    requirements: tuple[FactRequirement, ...]
    candidates: tuple[FactCandidate, ...]
    accepted_facts: tuple[FactRecord, ...]
    rejected_facts: tuple[FactRecord, ...]
    unresolved_requirement_ids: tuple[NonEmptyStr, ...]
    conflicts: tuple[FactConflict, ...]
    spans: tuple[EvidenceSpan, ...] = ()
    model_calls: tuple[ModelCallRecord, ...] = ()
