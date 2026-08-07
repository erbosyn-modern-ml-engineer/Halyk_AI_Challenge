"""Document taxonomy, lifecycle, and authority domain models (Stage 5C)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from halyk_agent.domain.authority.constants import (
    AUTHORITY_ALGORITHM_VERSION,
    AUTHORITY_RULE_VERSION,
    AUTHORITY_SCHEMA_VERSION,
    TAXONOMY_RULE_VERSION,
)
from halyk_agent.domain.common import NonEmptyStr


class DocumentType(StrEnum):
    LOAN_AGREEMENT = "LOAN_AGREEMENT"
    AUDITOR_REPORT = "AUDITOR_REPORT"
    KYC_DOSSIER = "KYC_DOSSIER"
    KYC_POLICY_OR_PROCEDURE = "KYC_POLICY_OR_PROCEDURE"
    TREASURY_MEMO = "TREASURY_MEMO"
    AGREED_UPON_PROCEDURES_REPORT = "AGREED_UPON_PROCEDURES_REPORT"
    GROUP_OR_CONSOLIDATED_REPORT = "GROUP_OR_CONSOLIDATED_REPORT"
    CORPORATE_REPORT = "CORPORATE_REPORT"
    BOARD_OR_INTERNAL_MEMO = "BOARD_OR_INTERNAL_MEMO"
    PRESS_RELEASE = "PRESS_RELEASE"
    IT_OR_OPERATIONS_DOCUMENT = "IT_OR_OPERATIONS_DOCUMENT"
    HR_OR_ADMIN_DOCUMENT = "HR_OR_ADMIN_DOCUMENT"
    OTHER_BUSINESS_DOCUMENT = "OTHER_BUSINESS_DOCUMENT"
    UNKNOWN = "UNKNOWN"


class DocumentLifecycleStatus(StrEnum):
    CURRENT_EXECUTED = "CURRENT_EXECUTED"
    FINAL = "FINAL"
    APPROVED = "APPROVED"
    EFFECTIVE = "EFFECTIVE"
    CURRENT = "CURRENT"
    DRAFT = "DRAFT"
    PRELIMINARY = "PRELIMINARY"
    WORKING_PAPER = "WORKING_PAPER"
    SUPERSEDED = "SUPERSEDED"
    OBSOLETE = "OBSOLETE"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"


class AuthorityDomain(StrEnum):
    COVENANT_TERMS = "COVENANT_TERMS"
    FINANCIAL_ADJUSTMENTS = "FINANCIAL_ADJUSTMENTS"
    KYC_RELATIONSHIPS = "KYC_RELATIONSHIPS"
    GROUP_STRUCTURE = "GROUP_STRUCTURE"
    TREASURY_FACTS = "TREASURY_FACTS"
    GENERAL_CONTEXT = "GENERAL_CONTEXT"
    NONE = "NONE"


class ClassificationConfidence(StrEnum):
    EXACT = "EXACT"
    DECLARED = "DECLARED"
    DERIVED = "DERIVED"
    WEAK = "WEAK"
    UNRESOLVED = "UNRESOLVED"


class AuthorityStatus(StrEnum):
    AUTHORITATIVE = "AUTHORITATIVE"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"
    MISSING_AUTHORITY = "MISSING_AUTHORITY"
    UNRESOLVED = "UNRESOLVED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class DocumentMetadata(BaseModel):
    """Deterministic metadata extracted from canonical document text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: NonEmptyStr
    document_version_id: NonEmptyStr
    source_file: NonEmptyStr
    source_sha256: NonEmptyStr
    title: NonEmptyStr | None = None
    subtitle: NonEmptyStr | None = None
    document_date: NonEmptyStr | None = None
    effective_date: NonEmptyStr | None = None
    execution_date: NonEmptyStr | None = None
    report_date: NonEmptyStr | None = None
    period_covered: NonEmptyStr | None = None
    version_indicator: NonEmptyStr | None = None
    report_number: NonEmptyStr | None = None
    agreement_number: NonEmptyStr | None = None
    draft_final_marker: NonEmptyStr | None = None
    superseded_marker: NonEmptyStr | None = None
    cross_reference: NonEmptyStr | None = None
    evidence_span_ids: tuple[NonEmptyStr, ...] = ()


class DocumentClassification(BaseModel):
    """Evidence-backed taxonomy + lifecycle + domain assignment for one document."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: NonEmptyStr
    document_version_id: NonEmptyStr
    scenario_ids: tuple[NonEmptyStr, ...] = ()
    document_type: DocumentType
    lifecycle_status: DocumentLifecycleStatus
    authority_domains: tuple[AuthorityDomain, ...] = ()
    confidence: ClassificationConfidence
    rule_id: NonEmptyStr
    reason_code: NonEmptyStr
    evidence_span_ids: tuple[NonEmptyStr, ...] = ()
    group_document_routing: bool = False


class DocumentFamily(BaseModel):
    """Logical document family within a scenario (e.g. loan agreement chain)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    family_id: NonEmptyStr
    scenario_id: NonEmptyStr
    family_kind: NonEmptyStr
    family_key: NonEmptyStr
    document_ids: tuple[NonEmptyStr, ...] = ()
    agreement_number: NonEmptyStr | None = None


class AuthorityDecision(BaseModel):
    """Deterministic authority decision for one scenario + domain."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_id: NonEmptyStr
    scenario_id: NonEmptyStr
    domain: AuthorityDomain
    status: AuthorityStatus
    rule_id: NonEmptyStr
    reason: NonEmptyStr
    winning_document_ids: tuple[NonEmptyStr, ...] = ()
    rejected_document_ids: tuple[NonEmptyStr, ...] = ()
    candidate_document_ids: tuple[NonEmptyStr, ...] = ()
    evidence_span_ids: tuple[NonEmptyStr, ...] = ()
    family_id: NonEmptyStr | None = None


class AuthorityConflict(BaseModel):
    """First-class unresolved authority conflict — never silently chosen."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    conflict_id: NonEmptyStr
    scenario_id: NonEmptyStr
    domain: AuthorityDomain
    candidate_document_ids: tuple[NonEmptyStr, ...] = ()
    evidence_span_ids: tuple[NonEmptyStr, ...] = ()
    reason: NonEmptyStr
    resolution_status: AuthorityStatus = AuthorityStatus.UNRESOLVED


class AuthorityEvidenceAssertion(BaseModel):
    """Persisted auditable taxonomy/authority evidence assertion."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    assertion_id: NonEmptyStr
    document_id: NonEmptyStr
    scenario_id: NonEmptyStr | None = None
    assertion_kind: NonEmptyStr
    document_type: DocumentType | None = None
    lifecycle_status: DocumentLifecycleStatus | None = None
    authority_domain: AuthorityDomain | None = None
    rule_id: NonEmptyStr
    reason_code: NonEmptyStr
    evidence_span_id: NonEmptyStr
    raw_quote: NonEmptyStr
    page_number: int = Field(ge=1)
    text_origin: NonEmptyStr
    source_sha256: NonEmptyStr
    ocr_backend_identity: str | None = None


class AuthorityManifest(BaseModel):
    """Deterministic Stage 5C identity manifest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: NonEmptyStr = AUTHORITY_SCHEMA_VERSION
    routing_manifest_hash: NonEmptyStr
    canonical_documents_hash: NonEmptyStr
    identity_evidence_hash: str = ""
    taxonomy_rule_version: NonEmptyStr = TAXONOMY_RULE_VERSION
    authority_rule_version: NonEmptyStr = AUTHORITY_RULE_VERSION
    authority_algorithm_version: NonEmptyStr = AUTHORITY_ALGORITHM_VERSION
    document_count: int = Field(ge=0)
    classified_count: int = Field(ge=0)
    unknown_count: int = Field(ge=0)
    decision_count: int = Field(ge=0)
    conflict_count: int = Field(ge=0)
    missing_authority_count: int = Field(ge=0)
    family_count: int = Field(ge=0)
    evidence_count: int = Field(ge=0)
    parsed_input_identity: dict[str, Any] = Field(default_factory=dict)


class AuthorityReport(BaseModel):
    """Full in-memory Stage 5C result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest: AuthorityManifest
    metadata: tuple[DocumentMetadata, ...] = ()
    classifications: tuple[DocumentClassification, ...] = ()
    families: tuple[DocumentFamily, ...] = ()
    decisions: tuple[AuthorityDecision, ...] = ()
    conflicts: tuple[AuthorityConflict, ...] = ()
    evidence: tuple[AuthorityEvidenceAssertion, ...] = ()
    spans: tuple[Any, ...] = ()

    @model_validator(mode="after")
    def _sorted_invariants(self) -> AuthorityReport:
        return self
