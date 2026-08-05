"""CLI / app indexing and search smoke tests (FAST; fake embeddings)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from halyk_agent.adapters.embeddings.model_registry import (
    FAST_EMBEDDING_LOGICAL_NAME,
    resolve_embedding_identity,
)
from halyk_agent.app.indexing import IndexingError, index_parsed_directory
from halyk_agent.app.retrieval import search_index
from halyk_agent.domain.embeddings import EmbeddingModelIdentity
from halyk_agent.domain.retrieval import IndexReport, RetrievalResult
from tests.embeddings.test_prefixes_and_validation import FakeProvider
from tests.ingestion.helpers import write_zip
from tests.parsing.helpers import make_text_pdf

ROOT = Path(__file__).resolve().parents[2]


def _parsed_dir_with_limit_text(tmp_path: Path) -> Path:
    pdf = make_text_pdf(
        [
            "Dogovor limit po operaciyam klienta equals one million tenge.",
            "Contract limit for client operations is one million tenge.",
        ]
    )
    archive = tmp_path / "docs.zip"
    write_zip(
        archive,
        {
            "limit_policy.pdf": pdf,
            "limit_ru.txt": (b"Dogovor limitt po operaciyam: see Contract limit terms.\n"),
        },
    )
    inspection = tmp_path / "inspection"
    parsed = tmp_path / "parsed"
    for args in (
        ["inspect", "--input", str(archive), "--output", str(inspection)],
        [
            "parse",
            "--inspection",
            str(inspection),
            "--output",
            str(parsed),
            "--profile",
            "fast",
        ],
    ):
        result = subprocess.run(
            [sys.executable, "-m", "halyk_agent", *args],
            check=False,
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        assert result.returncode == 0, result.stderr
    return parsed


@pytest.mark.asyncio
async def test_fast_index_and_search_with_fake_embeddings(tmp_path: Path) -> None:
    parsed = _parsed_dir_with_limit_text(tmp_path)
    out = tmp_path / "retrieval"
    identity = resolve_embedding_identity(FAST_EMBEDDING_LOGICAL_NAME)
    test_identity = EmbeddingModelIdentity(
        logical_name=identity.logical_name,
        model_id=identity.model_id,
        revision=identity.revision,
        dimension=8,
        max_input_tokens=identity.max_input_tokens,
        normalized=identity.normalized,
        query_prefix=identity.query_prefix,
        passage_prefix=identity.passage_prefix,
        license=identity.license,
    )
    report = await index_parsed_directory(
        parsed,
        out,
        profile="fast",
        overwrite=True,
        embedding_provider=FakeProvider(test_identity),
    )
    assert isinstance(report, IndexReport)
    assert report.chunk_count > 0
    assert (out / "chunks.jsonl").is_file()
    assert (out / "chunk_manifest.json").is_file()
    assert (out / "index_report.json").is_file()
    assert (out / "local_index.sqlite").is_file()
    assert (out / "retrieval_summary.md").is_file()
    IndexReport.model_validate_json((out / "index_report.json").read_text(encoding="utf-8"))

    result = await search_index(
        index_dir=out,
        query_text="Contract limit",
        profile="fast",
        top_k=5,
        lexical_only=True,
    )
    assert isinstance(result, RetrievalResult)
    assert result.hits
    assert result.hits[0].final_rank == 1


@pytest.mark.asyncio
async def test_index_rejects_nonempty_output_without_overwrite(tmp_path: Path) -> None:
    parsed = _parsed_dir_with_limit_text(tmp_path)
    out = tmp_path / "retrieval"
    out.mkdir()
    (out / "sentinel.txt").write_text("x", encoding="utf-8")
    identity = resolve_embedding_identity(FAST_EMBEDDING_LOGICAL_NAME)
    test_identity = identity.model_copy(update={"dimension": 8})
    with pytest.raises(IndexingError, match="overwrite"):
        await index_parsed_directory(
            parsed,
            out,
            profile="fast",
            overwrite=False,
            embedding_provider=FakeProvider(test_identity),
        )


def test_search_cli_help_and_missing_index(tmp_path: Path) -> None:
    help_result = subprocess.run(
        [sys.executable, "-m", "halyk_agent", "search", "--help"],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert help_result.returncode == 0
    assert "--query" in help_result.stdout

    missing = subprocess.run(
        [
            sys.executable,
            "-m",
            "halyk_agent",
            "search",
            "--index",
            str(tmp_path / "missing"),
            "--query",
            "test",
            "--profile",
            "fast",
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert missing.returncode != 0
