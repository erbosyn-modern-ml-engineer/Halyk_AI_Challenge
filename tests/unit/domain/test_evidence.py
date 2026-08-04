"""EvidenceSpan invariant tests."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from halyk_agent.domain.evidence import EvidenceSpan


def _base_kwargs() -> dict[str, object]:
    return {
        "id": "span-1",
        "source_file": "a.pdf",
        "document_id": "doc-1",
        "document_version_id": "ver-1",
        "page_number": 1,
        "quote": "valid quote",
    }


def test_evidence_span_rejects_empty_quote() -> None:
    kwargs = _base_kwargs()
    kwargs["quote"] = "   "
    with pytest.raises(ValidationError):
        EvidenceSpan(**kwargs)


def test_evidence_span_rejects_invalid_page_number() -> None:
    kwargs = _base_kwargs()
    kwargs["page_number"] = 0
    with pytest.raises(ValidationError):
        EvidenceSpan(**kwargs)


def test_evidence_span_rejects_partial_character_range() -> None:
    with pytest.raises(ValidationError, match="char_start and char_end"):
        EvidenceSpan(**_base_kwargs(), char_start=1, char_end=None)


def test_evidence_span_rejects_invalid_bbox() -> None:
    with pytest.raises(ValidationError):
        EvidenceSpan(**_base_kwargs(), bbox=(1.0, 2.0, math.nan, 4.0))


def test_evidence_span_rejects_inverted_character_range() -> None:
    with pytest.raises(ValidationError, match="char_start must be less than char_end"):
        EvidenceSpan(**_base_kwargs(), char_start=10, char_end=10)


def test_evidence_span_accepts_valid_span() -> None:
    span = EvidenceSpan(**_base_kwargs(), char_start=0, char_end=5, bbox=(0.0, 0.0, 1.0, 1.0))
    assert span.quote == "valid quote"
    assert span.page_number == 1
