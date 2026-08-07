"""Stage 5C document taxonomy and authority resolution."""

from __future__ import annotations

from halyk_agent.domain.authority.engine import run_authority
from halyk_agent.domain.authority.models import (
    AuthorityConflict,
    AuthorityDecision,
    AuthorityDomain,
    AuthorityEvidenceAssertion,
    AuthorityManifest,
    AuthorityReport,
    AuthorityStatus,
    ClassificationConfidence,
    DocumentClassification,
    DocumentFamily,
    DocumentLifecycleStatus,
    DocumentMetadata,
    DocumentType,
)

__all__ = [
    "AuthorityConflict",
    "AuthorityDecision",
    "AuthorityDomain",
    "AuthorityEvidenceAssertion",
    "AuthorityManifest",
    "AuthorityReport",
    "AuthorityStatus",
    "ClassificationConfidence",
    "DocumentClassification",
    "DocumentFamily",
    "DocumentLifecycleStatus",
    "DocumentMetadata",
    "DocumentType",
    "run_authority",
]
