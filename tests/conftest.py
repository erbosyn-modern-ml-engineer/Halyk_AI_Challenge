"""Shared pytest fixtures."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from halyk_agent.domain.documents import DocumentVersionRef, DocumentVersionStatus
from halyk_agent.domain.evidence import EvidenceSpan
from halyk_agent.domain.rules import RuleRef


@pytest.fixture
def sample_span() -> EvidenceSpan:
    return EvidenceSpan(
        id="span-1",
        source_file="docs/contract.pdf",
        document_id="doc-1",
        document_version_id="ver-1",
        page_number=1,
        quote="Payment due within 30 days",
        char_start=10,
        char_end=40,
    )


@pytest.fixture
def sample_version() -> DocumentVersionRef:
    return DocumentVersionRef(
        document_id="doc-1",
        version_id="ver-1",
        source_file="docs/contract.pdf",
        observed_at=datetime(2024, 1, 15, tzinfo=UTC),
        status=DocumentVersionStatus.EFFECTIVE,
        effective_from=datetime(2024, 1, 1, tzinfo=UTC),
        effective_to=datetime(2025, 1, 1, tzinfo=UTC),
    )


@pytest.fixture
def sample_rule() -> RuleRef:
    return RuleRef(rule_id="R-001", rule_version="1.0.0")


@pytest.fixture
def sample_decimal() -> Decimal:
    return Decimal("1234.56")
