"""Scenario and entity routing domain models (Stage 5B)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from halyk_agent.domain.common import NonEmptyStr

ROUTING_SCHEMA_VERSION = "halyk.routing_manifest.v1"
ROUTING_ALGORITHM_VERSION = "halyk.routing.v2"
NORMALIZATION_VERSION = "halyk.legal_name_norm.v2"


class ResolutionMethod(StrEnum):
    TXN_ID_PREFIX = "TXN_ID_PREFIX"
    ACCOUNT_ID_COLUMN = "ACCOUNT_ID_COLUMN"
    ACCOUNT_ID_FALLBACK = "ACCOUNT_ID_FALLBACK"
    TXN_ID_ACCOUNT_CONSISTENT = "TXN_ID_ACCOUNT_CONSISTENT"
    TXN_ID_ACCOUNT_CONFLICT = "TXN_ID_ACCOUNT_CONFLICT"
    EXPLICIT_ACCOUNT_ID = "EXPLICIT_ACCOUNT_ID"
    EXPLICIT_BORROWER_DECLARATION = "EXPLICIT_BORROWER_DECLARATION"
    DECLARED_ENTITY_RELATION = "DECLARED_ENTITY_RELATION"
    NORMALIZED_LEGAL_NAME = "NORMALIZED_LEGAL_NAME"
    GROUP_SEGMENT_DECLARATION = "GROUP_SEGMENT_DECLARATION"
    UNRESOLVED = "UNRESOLVED"


class ResolutionConfidence(StrEnum):
    EXACT = "EXACT"
    DECLARED = "DECLARED"
    DERIVED = "DERIVED"
    WEAK = "WEAK"
    UNRESOLVED = "UNRESOLVED"


class AliasKind(StrEnum):
    LEGAL_SUFFIX = "LEGAL_SUFFIX"
    PUNCTUATION = "PUNCTUATION"
    QUOTED_NAME = "QUOTED_NAME"
    ABBREVIATION = "ABBREVIATION"
    TRANSLITERATION = "TRANSLITERATION"
    EXPLICIT_DECLARATION = "EXPLICIT_DECLARATION"


class ConflictKind(StrEnum):
    MULTI_SCENARIO_DOCUMENT = "MULTI_SCENARIO_DOCUMENT"
    IDENTIFIER_NAME_CONFLICT = "IDENTIFIER_NAME_CONFLICT"
    BORROWER_NAME_CONFLICT = "BORROWER_NAME_CONFLICT"
    TRANSACTION_ACCOUNT_CONFLICT = "TRANSACTION_ACCOUNT_CONFLICT"
    DUPLICATE_TXN_ID = "DUPLICATE_TXN_ID"
    ACCOUNT_ID_MALFORMED = "ACCOUNT_ID_MALFORMED"
    SCENARIO_WITHOUT_ACCOUNT = "SCENARIO_WITHOUT_ACCOUNT"
    SCENARIO_WITH_MULTIPLE_ACCOUNTS = "SCENARIO_WITH_MULTIPLE_ACCOUNTS"
    LEGAL_FORM_MISMATCH = "LEGAL_FORM_MISMATCH"


class DiagnosticCode(StrEnum):
    SCENARIO_WITHOUT_ACCOUNT = "SCENARIO_WITHOUT_ACCOUNT"
    SCENARIO_WITH_MULTIPLE_ACCOUNTS = "SCENARIO_WITH_MULTIPLE_ACCOUNTS"
    TRANSACTION_UNKNOWN_SCENARIO = "TRANSACTION_UNKNOWN_SCENARIO"
    TRANSACTION_ACCOUNT_CONFLICT = "TRANSACTION_ACCOUNT_CONFLICT"
    ACCOUNT_WITHOUT_SCENARIO_TOKEN = "ACCOUNT_WITHOUT_SCENARIO_TOKEN"
    DOCUMENT_NO_ENTITY_SIGNAL = "DOCUMENT_NO_ENTITY_SIGNAL"
    DOCUMENT_MULTIPLE_SCENARIOS = "DOCUMENT_MULTIPLE_SCENARIOS"
    DOCUMENT_IDENTIFIER_NAME_CONFLICT = "DOCUMENT_IDENTIFIER_NAME_CONFLICT"
    BORROWER_NAME_CONFLICT = "BORROWER_NAME_CONFLICT"
    ACCOUNT_ID_MALFORMED = "ACCOUNT_ID_MALFORMED"
    OCR_IDENTITY_LOW_TRUST = "OCR_IDENTITY_LOW_TRUST"
    LEGAL_FORM_MISMATCH = "LEGAL_FORM_MISMATCH"
    GROUP_DOCUMENT = "GROUP_DOCUMENT"


class DiagnosticSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class ScenarioIdentity(BaseModel):
    """Opaque scenario identifier discovered from the submission template."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: NonEmptyStr
    required_covenant_ids: tuple[NonEmptyStr, ...] = ()
    ordinal: int = Field(ge=0)


class AccountIdentity(BaseModel):
    """Normalized account identifier with optional document provenance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    account_id_raw: NonEmptyStr
    account_id_normalized: NonEmptyStr
    document_id: NonEmptyStr | None = None
    document_version_id: NonEmptyStr | None = None
    page_number: int | None = Field(default=None, ge=1)
    evidence_span_id: NonEmptyStr | None = None
    text_origin: NonEmptyStr | None = None
    ocr_backend_identity: str | None = None
    ocr_configuration_hash: str | None = None


class BorrowerIdentity(BaseModel):
    """Borrower declaration / mention candidate (authority deferred to Stage 5C)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    legal_name_raw: NonEmptyStr
    normalized_name: NonEmptyStr
    identity_key: NonEmptyStr
    base_key: NonEmptyStr
    declaration_type: NonEmptyStr
    document_id: NonEmptyStr
    document_version_id: NonEmptyStr
    page_number: int = Field(ge=1)
    evidence_span_id: NonEmptyStr
    account_id_normalized: NonEmptyStr | None = None
    scenario_id: NonEmptyStr | None = None
    anchored: bool = False


class CompanyAlias(BaseModel):
    """Recorded deterministic alias transform — never a silent merge."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical_candidate: NonEmptyStr
    variant: NonEmptyStr
    alias_kind: AliasKind
    derived: bool = True
    source_document_id: NonEmptyStr | None = None
    evidence_span_id: NonEmptyStr | None = None


class CounterpartyIdentity(BaseModel):
    """Ledger counterparty identity (classification deferred)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    counterparty_raw: NonEmptyStr
    counterparty_normalized: NonEmptyStr
    occurrence_count: int = Field(ge=1)
    txn_ids: tuple[NonEmptyStr, ...] = ()
    scenario_ids: tuple[NonEmptyStr, ...] = ()
    account_ids: tuple[NonEmptyStr, ...] = ()


class DocumentEntityLink(BaseModel):
    """Document ↔ scenario link with categorical confidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: NonEmptyStr
    document_version_id: NonEmptyStr
    scenario_ids: tuple[NonEmptyStr, ...] = ()
    account_ids: tuple[NonEmptyStr, ...] = ()
    method: ResolutionMethod
    confidence: ResolutionConfidence
    evidence_span_ids: tuple[NonEmptyStr, ...] = ()
    notes: NonEmptyStr | None = None
    group_document: bool = False
    relation_type: NonEmptyStr | None = None


class TransactionEntityLink(BaseModel):
    """Transaction ↔ scenario link with ledger row provenance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    txn_id: NonEmptyStr
    row_index: int = Field(ge=0)
    ledger_source_file: NonEmptyStr
    account_id_raw: NonEmptyStr
    account_id_normalized: NonEmptyStr
    scenario_id: NonEmptyStr | None = None
    scenario_token: NonEmptyStr | None = None
    method: ResolutionMethod
    confidence: ResolutionConfidence
    counterparty_raw: NonEmptyStr


class EntityResolutionConflict(BaseModel):
    """First-class conflict — never silently resolved by guessing."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    conflict_id: NonEmptyStr
    kind: ConflictKind
    severity: DiagnosticSeverity
    scenario_ids: tuple[NonEmptyStr, ...] = ()
    account_ids: tuple[NonEmptyStr, ...] = ()
    document_ids: tuple[NonEmptyStr, ...] = ()
    txn_ids: tuple[NonEmptyStr, ...] = ()
    evidence_span_ids: tuple[NonEmptyStr, ...] = ()
    detail: NonEmptyStr


class RoutingDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: DiagnosticCode
    severity: DiagnosticSeverity
    message: NonEmptyStr
    scenario_id: NonEmptyStr | None = None
    document_id: NonEmptyStr | None = None
    txn_id: NonEmptyStr | None = None
    account_id: NonEmptyStr | None = None


class ScenarioRoutingRecord(BaseModel):
    """Per-scenario routing aggregate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: NonEmptyStr
    ordinal: int = Field(ge=0)
    required_covenant_ids: tuple[NonEmptyStr, ...] = ()
    account_ids: tuple[NonEmptyStr, ...] = ()
    transaction_count: int = Field(ge=0)
    document_ids: tuple[NonEmptyStr, ...] = ()
    borrower_normalized_names: tuple[NonEmptyStr, ...] = ()


class RoutingManifest(BaseModel):
    """Deterministic routing identity manifest (no timestamps in hash inputs)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: NonEmptyStr = ROUTING_SCHEMA_VERSION
    dataset_manifest_hash: NonEmptyStr
    canonical_documents_hash: NonEmptyStr
    evidence_catalogue_hash: NonEmptyStr = ""
    parsed_input_identity: dict[str, object] = Field(default_factory=dict)
    routing_algorithm_version: NonEmptyStr = ROUTING_ALGORITHM_VERSION
    normalization_version: NonEmptyStr = NORMALIZATION_VERSION
    scenario_count: int = Field(ge=0)
    resolved_document_count: int = Field(ge=0)
    unresolved_document_count: int = Field(ge=0)
    transaction_link_count: int = Field(ge=0)
    conflict_count: int = Field(ge=0)
    template_cell_count: int = Field(ge=0)
    ledger_row_count: int = Field(ge=0)
    scenario_transaction_count: int = Field(ge=0)
    txn_id_linked_count: int = Field(default=0, ge=0)
    account_fallback_count: int = Field(default=0, ge=0)
    multi_scenario_document_count: int = Field(ge=0)
    identity_evidence_count: int = Field(default=0, ge=0)


class IdentityEvidenceAssertion(BaseModel):
    """Persisted auditable identity/routing assertion (Stage 5B.2)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    assertion_id: NonEmptyStr
    scenario_id: NonEmptyStr | None = None
    document_id: NonEmptyStr | None = None
    txn_id: NonEmptyStr | None = None
    account_id: NonEmptyStr | None = None
    identity_type: NonEmptyStr
    resolution_method: ResolutionMethod
    confidence: ResolutionConfidence | None = None
    # Document-span provenance (EvidenceSpan). Optional for ledger-row assertions.
    evidence_span_id: NonEmptyStr | None = None
    raw_quote: NonEmptyStr | None = None
    page_number: int | None = Field(default=None, ge=1)
    text_origin: NonEmptyStr | None = None
    source_sha256: NonEmptyStr | None = None
    ocr_backend_identity: str | None = None
    # Structured ledger-row provenance (not an EvidenceSpan).
    provenance_kind: NonEmptyStr = "document_span"
    ledger_source_file: NonEmptyStr | None = None
    ledger_row_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _validate_provenance(self) -> IdentityEvidenceAssertion:
        kind = self.provenance_kind
        if kind == "document_span":
            if (
                not self.evidence_span_id
                or not self.raw_quote
                or self.page_number is None
                or not self.text_origin
            ):
                raise ValueError(
                    "document_span assertion requires evidence_span_id, raw_quote, "
                    "page_number, and text_origin"
                )
            if self.document_id and not self.source_sha256:
                raise ValueError("document-derived identity assertion requires source_sha256")
        elif kind == "ledger_row":
            if not self.txn_id or self.ledger_source_file is None or self.ledger_row_index is None:
                raise ValueError(
                    "ledger_row assertion requires txn_id, ledger_source_file, and ledger_row_index"
                )
            if self.evidence_span_id is not None or self.raw_quote is not None:
                raise ValueError("ledger_row assertion must not invent EvidenceSpan fields")
        else:
            raise ValueError(f"unknown provenance_kind: {kind}")
        return self


class RoutingReport(BaseModel):
    """Full in-memory routing result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest: RoutingManifest
    scenarios: tuple[ScenarioIdentity, ...]
    scenario_routes: tuple[ScenarioRoutingRecord, ...]
    transaction_links: tuple[TransactionEntityLink, ...]
    document_links: tuple[DocumentEntityLink, ...]
    borrowers: tuple[BorrowerIdentity, ...]
    aliases: tuple[CompanyAlias, ...]
    counterparties: tuple[CounterpartyIdentity, ...]
    conflicts: tuple[EntityResolutionConflict, ...]
    diagnostics: tuple[RoutingDiagnostic, ...]
    account_extractions: tuple[AccountIdentity, ...] = ()
    identity_evidence: tuple[IdentityEvidenceAssertion, ...] = ()
    identity_spans: tuple[Any, ...] = ()

    @model_validator(mode="after")
    def _counts_agree(self) -> RoutingReport:
        if self.manifest.scenario_count != len(self.scenarios):
            raise ValueError("manifest.scenario_count mismatch")
        if self.manifest.conflict_count != len(self.conflicts):
            raise ValueError("manifest.conflict_count mismatch")
        return self


class LedgerRow(BaseModel):
    """Typed ledger row consumed by routing (I/O-free domain input)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    row_index: int = Field(ge=0)
    txn_id: NonEmptyStr
    date: NonEmptyStr
    account_id: NonEmptyStr
    counterparty: NonEmptyStr
    description: str = ""
    amount: str = ""
    currency: NonEmptyStr
    ledger_source_file: NonEmptyStr


class TxnIdParserConfig(BaseModel):
    """Configurable txn-id scenario-token extractor."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    pattern: NonEmptyStr = r"^TXN-(?P<scenario>[A-Za-z0-9]+)-(?P<seq>\d+)$"

    @field_validator("pattern")
    @classmethod
    def _must_have_scenario_group(cls, value: str) -> str:
        if "(?P<scenario>" not in value:
            raise ValueError("txn-id pattern must define a named group 'scenario'")
        return value
