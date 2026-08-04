"""Dataset manifest and schema-profile domain models."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from halyk_agent.domain.common import NonEmptyStr


class ArtifactRole(StrEnum):
    """Heuristic dataset role classification (non-authoritative)."""

    DOCUMENT = "DOCUMENT"
    TRANSACTION_TABLE = "TRANSACTION_TABLE"
    CASE_DEFINITION = "CASE_DEFINITION"
    SCORING_RULES = "SCORING_RULES"
    SUBMISSION_TEMPLATE = "SUBMISSION_TEMPLATE"
    METADATA = "METADATA"
    NESTED_ARCHIVE = "NESTED_ARCHIVE"
    UNKNOWN = "UNKNOWN"


class ArtifactFormat(StrEnum):
    """Detected artifact format."""

    PDF = "PDF"
    DOCX = "DOCX"
    XLSX = "XLSX"
    CSV = "CSV"
    JSON = "JSON"
    JSONL = "JSONL"
    TXT = "TXT"
    ZIP = "ZIP"
    IMAGE = "IMAGE"
    UNKNOWN = "UNKNOWN"


class PrimitiveType(StrEnum):
    """Sample-inferred primitive column type."""

    NULL = "NULL"
    BOOLEAN = "BOOLEAN"
    INTEGER = "INTEGER"
    DECIMAL = "DECIMAL"
    DATETIME = "DATETIME"
    DATE = "DATE"
    STRING = "STRING"
    MIXED = "MIXED"


class SemanticType(StrEnum):
    """Candidate semantic labels for columns or JSON keys."""

    RECORD_ID = "record_id"
    TRANSACTION_ID = "transaction_id"
    DOCUMENT_ID = "document_id"
    CASE_ID = "case_id"
    ENTITY_ID = "entity_id"
    COUNTERPARTY_ID = "counterparty_id"
    CONTRACT_ID = "contract_id"
    INVOICE_ID = "invoice_id"
    AMOUNT = "amount"
    CURRENCY = "currency"
    STATUS = "status"
    TRANSACTION_TYPE = "transaction_type"
    OCCURRED_AT = "occurred_at"
    POSTED_AT = "posted_at"
    SETTLED_AT = "settled_at"
    REVERSAL_OF_ID = "reversal_of_id"
    PARENT_TRANSACTION_ID = "parent_transaction_id"
    DESCRIPTION = "description"
    DECISION = "decision"
    EVIDENCE = "evidence"
    UNKNOWN = "unknown"


class SemanticCandidate(BaseModel):
    """Non-authoritative semantic typing hypothesis for a column or key."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    semantic_type: SemanticType
    confidence: float = Field(ge=0.0, le=1.0)
    reasons: list[NonEmptyStr] = Field(default_factory=list)


class ColumnProfile(BaseModel):
    """Sample-based profile of one tabular column."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: NonEmptyStr
    normalized_name: NonEmptyStr
    position: int = Field(ge=0)
    primitive_type: PrimitiveType
    nullable: bool
    sample_non_null_count: int = Field(ge=0)
    sample_null_count: int = Field(ge=0)
    sample_distinct_count: int = Field(ge=0)
    examples: list[str] = Field(default_factory=list, max_length=3)
    minimum: str | None = None
    maximum: str | None = None
    semantic_candidates: list[SemanticCandidate] = Field(default_factory=list)

    @field_validator("examples")
    @classmethod
    def _no_binary_examples(cls, value: list[str]) -> list[str]:
        for item in value:
            if "\x00" in item:
                raise ValueError("examples must not contain NUL/binary data")
        return value


class SheetProfile(BaseModel):
    """Sample-based profile of one spreadsheet sheet."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: NonEmptyStr
    estimated_rows: int = Field(ge=0)
    estimated_columns: int = Field(ge=0)
    sampled_rows: int = Field(ge=0)
    columns: list[ColumnProfile] = Field(default_factory=list)
    formula_cell_count: int = Field(ge=0)


class TableProfile(BaseModel):
    """Schema profile for a tabular or structured artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: NonEmptyStr
    format: ArtifactFormat
    encoding: NonEmptyStr | None = None
    delimiter: NonEmptyStr | None = None
    header_detected: bool | None = None
    sampled_rows: int = Field(default=0, ge=0)
    columns: list[ColumnProfile] = Field(default_factory=list)
    sheets: list[SheetProfile] = Field(default_factory=list)
    warnings: list[NonEmptyStr] = Field(default_factory=list)


class DatasetArtifact(BaseModel):
    """One inventoried archive member with optional schema profile."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: NonEmptyStr
    relative_path: NonEmptyStr
    normalized_path: NonEmptyStr
    format: ArtifactFormat
    mime_type: NonEmptyStr | None = None
    role: ArtifactRole
    role_confidence: float = Field(ge=0.0, le=1.0)
    role_reasons: list[NonEmptyStr] = Field(default_factory=list)
    size_bytes: int = Field(ge=0)
    compressed_size_bytes: int = Field(ge=0)
    sha256: NonEmptyStr
    table_profile: TableProfile | None = None
    warnings: list[NonEmptyStr] = Field(default_factory=list)


class DatasetManifest(BaseModel):
    """Deterministic inventory of a competition archive."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: NonEmptyStr = "halyk.dataset_manifest.v1"
    archive_name: NonEmptyStr
    archive_sha256: NonEmptyStr
    artifacts: list[DatasetArtifact] = Field(default_factory=list)
    total_files: int = Field(ge=0)
    total_uncompressed_bytes: int = Field(ge=0)
    warnings: list[NonEmptyStr] = Field(default_factory=list)


class SchemaProfileDocument(BaseModel):
    """Machine-readable collection of table profiles for an archive."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: NonEmptyStr = "halyk.schema_profile.v1"
    archive_sha256: NonEmptyStr
    tables: list[TableProfile] = Field(default_factory=list)
    warnings: list[NonEmptyStr] = Field(default_factory=list)


class InspectionResult(BaseModel):
    """Outputs produced by archive inspection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest: DatasetManifest
    schema_profile: SchemaProfileDocument
    summary_path: NonEmptyStr
