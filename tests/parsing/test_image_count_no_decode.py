"""MEDIUM-1: count pypdf images via len() without materialising ImageFile objects."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from halyk_agent.adapters.archive.hashing import sha256_bytes
from halyk_agent.adapters.parsing.post_parse_gate import apply_post_parse_quality_gate
from halyk_agent.adapters.parsing.pypdf_parser import PyPdfDocumentParser
from halyk_agent.contracts.parsing import ParseRequest
from halyk_agent.domain.datasets import ArtifactFormat
from halyk_agent.domain.page_quality import ImageVisibility, page_image_count_from_pypdf
from halyk_agent.domain.parsing import ParseStatus

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_SCANNED = ROOT / "agentic-bank-public" / "documents" / "f3fa6d20c8a1.pdf"


class _LazyImages:
    """Sequence that supports len() but must never be iterated or indexed."""

    def __len__(self) -> int:
        return 3

    def __iter__(self) -> Iterator[object]:
        raise AssertionError("image counting must not iterate page.images")

    def __getitem__(self, index: int) -> object:
        raise AssertionError(f"image counting must not index page.images[{index}]")


def test_page_image_count_uses_len_without_iteration() -> None:
    page = SimpleNamespace(images=_LazyImages())
    # No /Resources → only page.images path contributes.
    page.get = lambda _key: None  # type: ignore[method-assign]
    count, visibility, warnings = page_image_count_from_pypdf(page)
    assert count == 3
    assert visibility is ImageVisibility.KNOWN
    assert warnings == []


@pytest.mark.asyncio
async def test_scanned_fixture_counts_remain_known_without_decode() -> None:
    if not PUBLIC_SCANNED.is_file():
        pytest.skip("public training dataset absent — f3fa6d20c8a1.pdf not found")
    data = PUBLIC_SCANNED.read_bytes()
    parser = PyPdfDocumentParser()
    candidate, visuals = parser.parse_with_visuals(
        data,
        source_file=PUBLIC_SCANNED.name,
        artifact_id="scan",
        source_sha256=sha256_bytes(data),
    )
    assert [v.image_count for v in visuals] == [3, 3, 3]
    assert all(v.image_visibility is ImageVisibility.KNOWN for v in visuals)
    gated = apply_post_parse_quality_gate(candidate, page_visuals=visuals)
    assert gated.document.status is ParseStatus.FAILED
    assert {issue.page_number for issue in gated.summary.issues} == {1, 2, 3}

    result = await parser.parse(
        ParseRequest(
            artifact_id="scan",
            source_file=PUBLIC_SCANNED.name,
            source_path=PUBLIC_SCANNED,
            source_sha256=sha256_bytes(data),
            format=ArtifactFormat.PDF,
            mime_type="application/pdf",
        )
    )
    assert result.selected_document is not None
    assert result.selected_document.status is ParseStatus.FAILED
