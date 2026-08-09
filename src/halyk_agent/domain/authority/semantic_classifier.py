"""Bounded semantic fallback for unresolved document taxonomy/lifecycle.

The model may propose only enum values plus one exact source quote.  It never
chooses authority winners or resolves conflicts; the existing deterministic
resolver remains authoritative.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from halyk_agent.config import Settings
from halyk_agent.domain.authority.evidence import require_span_or_none
from halyk_agent.domain.authority.models import (
    DocumentClassification,
    DocumentLifecycleStatus,
    DocumentMetadata,
    DocumentType,
)
from halyk_agent.domain.evidence import EvidenceSpan
from halyk_agent.domain.models_gateway.semantic_json import SemanticJsonGateway, SemanticJsonState
from halyk_agent.domain.parsing import CanonicalDocument


class _DocumentCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_type: str
    lifecycle_status: str
    confidence: Literal["HIGH", "MEDIUM", "LOW"]
    source_quote: str
    reason: str


@dataclass(frozen=True, slots=True)
class SemanticDocumentOverride:
    document_type: DocumentType
    lifecycle_status: DocumentLifecycleStatus
    evidence_span: EvidenceSpan
    reason: str


@dataclass(frozen=True, slots=True)
class SemanticDocumentBatch:
    overrides: dict[str, SemanticDocumentOverride]
    diagnostics: tuple[dict[str, str], ...]
    model_calls: int


_SYSTEM = (
    "You are a bounded document-taxonomy parser. Deterministic rules could not fully classify "
    "the supplied banking document. Choose only from the supplied document_type and lifecycle "
    "enums. Do not decide authority, do not choose between conflicting documents, and do not "
    "infer a lifecycle without explicit textual support. source_quote must be one exact contiguous "
    "quote from source_text that directly supports the proposed type/lifecycle. If uncertain, use "
    "UNKNOWN and/or LOW confidence. Return one JSON object only."
)


def _source_text(document: CanonicalDocument, *, max_chars: int = 12_000) -> str:
    parts: list[str] = []
    remaining = max_chars
    for page in document.pages:
        raw = page.raw_text or ""
        if not raw:
            continue
        piece = raw[:remaining]
        parts.append(piece)
        remaining -= len(piece)
        if remaining <= 0:
            break
    return "\n".join(parts)


def classify_unresolved_documents(
    *,
    documents: tuple[CanonicalDocument, ...],
    deterministic: dict[str, DocumentClassification],
    metadata: dict[str, DocumentMetadata],
    settings: Settings,
    gateway: SemanticJsonGateway | None = None,
) -> SemanticDocumentBatch:
    """Propose type/lifecycle only for deterministic UNKNOWN states."""
    if not settings.semantic_fallback_enabled:
        return SemanticDocumentBatch(overrides={}, diagnostics=(), model_calls=0)

    semantic_gateway = gateway or SemanticJsonGateway(settings=settings)
    overrides: dict[str, SemanticDocumentOverride] = {}
    diagnostics: list[dict[str, str]] = []
    model_calls = 0
    allowed_types = [item.value for item in DocumentType]
    allowed_lifecycle = [item.value for item in DocumentLifecycleStatus]

    for document in sorted(documents, key=lambda item: item.document_id):
        current = deterministic[document.document_id]
        needs_type = current.document_type is DocumentType.UNKNOWN
        needs_lifecycle = current.lifecycle_status is DocumentLifecycleStatus.UNKNOWN
        if not (needs_type or needs_lifecycle):
            continue
        text = _source_text(document)
        if not text.strip():
            continue
        meta = metadata[document.document_id]
        request = {
            "document_id": document.document_id,
            "source_text": text,
            "deterministic_document_type": current.document_type.value,
            "deterministic_lifecycle_status": current.lifecycle_status.value,
            "metadata": {
                "title": meta.title,
                "document_date": meta.document_date,
                "effective_date": meta.effective_date,
                "report_date": meta.report_date,
                "version_indicator": meta.version_indicator,
            },
            "allowed_document_types": allowed_types,
            "allowed_lifecycle_statuses": allowed_lifecycle,
            "constraints": {
                "preserve_known_type": not needs_type,
                "preserve_known_lifecycle": not needs_lifecycle,
                "unknown_is_allowed": True,
            },
            "output_schema": {
                "document_type": "one allowed document type",
                "lifecycle_status": "one allowed lifecycle status",
                "confidence": "HIGH|MEDIUM|LOW",
                "source_quote": "exact contiguous source quote",
                "reason": "short semantic reason",
            },
        }
        response = semantic_gateway.propose(
            task_id=f"document-taxonomy:{document.document_id}",
            prompt_version="authority-semantic-document-v1",
            schema_version="authority-document-enums-v1",
            source_sha256=document.source_sha256,
            system_prompt=_SYSTEM,
            request_payload=request,
            max_tokens=700,
        )
        model_calls += int(response.model_called)
        if (
            response.state not in {SemanticJsonState.RESOLVED, SemanticJsonState.CACHE_HIT}
            or response.payload is None
        ):
            diagnostics.append(
                {
                    "document_id": document.document_id,
                    "status": "UNRESOLVED",
                    "reason": response.reason_code,
                }
            )
            continue
        try:
            candidate = _DocumentCandidate.model_validate(response.payload)
            doc_type = DocumentType(candidate.document_type)
            lifecycle = DocumentLifecycleStatus(candidate.lifecycle_status)
        except (ValidationError, ValueError) as exc:
            diagnostics.append(
                {
                    "document_id": document.document_id,
                    "status": "UNRESOLVED",
                    "reason": f"CANDIDATE_SCHEMA_INVALID:{exc.__class__.__name__}",
                }
            )
            continue
        if candidate.confidence != "HIGH":
            diagnostics.append(
                {
                    "document_id": document.document_id,
                    "status": "UNRESOLVED",
                    "reason": "MODEL_NOT_HIGH_CONFIDENCE",
                }
            )
            continue
        if not needs_type and doc_type is not current.document_type:
            diagnostics.append(
                {
                    "document_id": document.document_id,
                    "status": "UNRESOLVED",
                    "reason": "KNOWN_TYPE_OVERRIDE_FORBIDDEN",
                }
            )
            continue
        if not needs_lifecycle and lifecycle is not current.lifecycle_status:
            diagnostics.append(
                {
                    "document_id": document.document_id,
                    "status": "UNRESOLVED",
                    "reason": "KNOWN_LIFECYCLE_OVERRIDE_FORBIDDEN",
                }
            )
            continue
        if (
            needs_type
            and doc_type is DocumentType.UNKNOWN
            and needs_lifecycle
            and lifecycle is DocumentLifecycleStatus.UNKNOWN
        ):
            diagnostics.append(
                {
                    "document_id": document.document_id,
                    "status": "UNRESOLVED",
                    "reason": "MODEL_RETURNED_UNKNOWN",
                }
            )
            continue
        if not candidate.source_quote or candidate.source_quote not in text:
            diagnostics.append(
                {
                    "document_id": document.document_id,
                    "status": "UNRESOLVED",
                    "reason": "SOURCE_QUOTE_NOT_EXACT",
                }
            )
            continue
        span = require_span_or_none(document, patterns=(candidate.source_quote,))
        if span is None:
            diagnostics.append(
                {
                    "document_id": document.document_id,
                    "status": "UNRESOLVED",
                    "reason": "SOURCE_QUOTE_SPAN_NOT_FOUND",
                }
            )
            continue
        overrides[document.document_id] = SemanticDocumentOverride(
            document_type=doc_type,
            lifecycle_status=lifecycle,
            evidence_span=span,
            reason=candidate.reason,
        )
        diagnostics.append(
            {
                "document_id": document.document_id,
                "status": "ACCEPTED",
                "document_type": doc_type.value,
                "lifecycle_status": lifecycle.value,
                "source_quote": span.quote,
                "reason": candidate.reason,
            }
        )

    return SemanticDocumentBatch(
        overrides=overrides,
        diagnostics=tuple(diagnostics),
        model_calls=model_calls,
    )
