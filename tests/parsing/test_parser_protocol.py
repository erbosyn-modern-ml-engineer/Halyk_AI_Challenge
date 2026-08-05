"""Protocol typing and parser-contract tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from halyk_agent.adapters.archive.hashing import sha256_bytes
from halyk_agent.adapters.parsing.docling_parser import DoclingDocumentParser
from halyk_agent.adapters.parsing.pypdf_parser import PyPdfDocumentParser
from halyk_agent.contracts.parsing import DocumentParser, ParseRequest
from halyk_agent.domain.datasets import ArtifactFormat
from halyk_agent.domain.parsing import ParseResult
from tests.parsing.helpers import make_text_pdf, write_bytes


def test_pypdf_parser_satisfies_document_parser_protocol() -> None:
    parser: DocumentParser = PyPdfDocumentParser()
    assert isinstance(parser, DocumentParser)


def test_docling_parser_satisfies_document_parser_protocol() -> None:
    parser: DocumentParser = DoclingDocumentParser()
    assert isinstance(parser, DocumentParser)


@pytest.mark.asyncio
async def test_pypdf_protocol_parse_returns_parse_result(tmp_path: Path) -> None:
    data = make_text_pdf(["protocol path parse"])
    path = write_bytes(tmp_path / "a.pdf", data)
    request = ParseRequest(
        artifact_id="art-1",
        source_path=path,
        format=ArtifactFormat.PDF,
        source_file="a.pdf",
        source_sha256=sha256_bytes(data),
        mime_type="application/pdf",
    )
    result = await PyPdfDocumentParser().parse(request)
    assert isinstance(result, ParseResult)
    assert result.selected_document is not None
    assert result.artifact_id == "art-1"


def test_no_competing_parsed_document_contract() -> None:
    import halyk_agent.contracts.parsing as parsing_contracts

    assert not hasattr(parsing_contracts, "ParsedDocument")


def test_pyproject_base_dependencies_exclude_docling() -> None:
    root = Path(__file__).resolve().parents[2]
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    # Main [project] dependencies block must not list docling.
    main = text.split("[project.optional-dependencies]")[0]
    assert "docling" not in main.lower()
