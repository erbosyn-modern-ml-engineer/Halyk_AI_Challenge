"""CLI and application parse workflow tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from halyk_agent.adapters.parsing.errors import (
    DocumentParsingError,
    ParserDependencyMissingError,
)
from halyk_agent.app.parsing import parse_inspection_directory
from halyk_agent.config import Settings
from halyk_agent.domain.parsing import CanonicalDocument, ParseBatchReport
from tests.ingestion.helpers import write_zip
from tests.parsing.helpers import make_text_pdf

ROOT = Path(__file__).resolve().parents[2]


def _inspection_with_pdf(tmp_path: Path) -> Path:
    pdf = make_text_pdf(["CLI parse document content alphanumeric"])
    archive = tmp_path / "docs.zip"
    write_zip(archive, {"policy.pdf": pdf, "notes.txt": b"plain notes"})
    inspection = tmp_path / "inspection"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "halyk_agent",
            "inspect",
            "--input",
            str(archive),
            "--output",
            str(inspection),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    return inspection


def test_parse_cli_creates_all_four_outputs(tmp_path: Path) -> None:
    inspection = _inspection_with_pdf(tmp_path)
    out = tmp_path / "parsed"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "halyk_agent",
            "parse",
            "--inspection",
            str(inspection),
            "--output",
            str(out),
            "--profile",
            "fast",
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    assert (out / "documents").is_dir()
    assert (out / "evidence_catalog.jsonl").is_file()
    assert (out / "parse_report.json").is_file()
    assert (out / "parsing_summary.md").is_file()


def test_fast_parse_works_without_requiring_docling(tmp_path: Path) -> None:
    inspection = _inspection_with_pdf(tmp_path)
    report = parse_inspection_directory(
        inspection,
        tmp_path / "out",
        profile="fast",
        settings=Settings(stage=3),
    )
    assert report.total_candidates >= 1
    assert report.successful + report.partial >= 1


def test_full_without_extra_exits_clearly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inspection = _inspection_with_pdf(tmp_path)
    from halyk_agent.adapters.parsing import docling_parser as dp

    class BoomParser:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def parser_identity(self):  # type: ignore[no-untyped-def]
            from halyk_agent.domain.parsing import ParserIdentity, ParserKind

            return ParserIdentity(
                kind=ParserKind.DOCLING,
                package_name="docling",
                package_version="missing",
                configuration_hash="x",
            )

        def parse_canonical(self, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
            raise ParserDependencyMissingError("Docling is not installed")

        def parse_with_visuals(self, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
            raise ParserDependencyMissingError("Docling is not installed")

    monkeypatch.setattr(dp, "DoclingDocumentParser", BoomParser)
    with pytest.raises(ParserDependencyMissingError):
        parse_inspection_directory(
            inspection,
            tmp_path / "full-svc",
            profile="full",
            force_docling=True,
            settings=Settings(stage=3),
        )


def test_force_docling_rejected_on_fast_profile(tmp_path: Path) -> None:
    inspection = _inspection_with_pdf(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "halyk_agent",
            "parse",
            "--inspection",
            str(inspection),
            "--output",
            str(tmp_path / "out"),
            "--profile",
            "fast",
            "--force-docling",
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode != 0


def test_nonempty_output_refused_without_overwrite(tmp_path: Path) -> None:
    inspection = _inspection_with_pdf(tmp_path)
    out = tmp_path / "parsed"
    out.mkdir()
    (out / "marker.txt").write_text("x", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "halyk_agent",
            "parse",
            "--inspection",
            str(inspection),
            "--output",
            str(out),
            "--profile",
            "fast",
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode != 0


def test_overwrite_succeeds(tmp_path: Path) -> None:
    inspection = _inspection_with_pdf(tmp_path)
    out = tmp_path / "parsed"
    out.mkdir()
    (out / "marker.txt").write_text("x", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "halyk_agent",
            "parse",
            "--inspection",
            str(inspection),
            "--output",
            str(out),
            "--profile",
            "fast",
            "--overwrite",
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr


def test_one_failed_document_does_not_erase_successful(tmp_path: Path) -> None:
    good = make_text_pdf(["good alphanumeric content here"])
    archive = tmp_path / "mix.zip"
    write_zip(archive, {"good.pdf": good, "bad.pdf": b"not-a-pdf"})
    inspection = tmp_path / "insp"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "halyk_agent",
            "inspect",
            "--input",
            str(archive),
            "--output",
            str(inspection),
        ],
        check=True,
        cwd=ROOT,
    )
    out = tmp_path / "parsed"
    report = parse_inspection_directory(inspection, out, profile="fast", settings=Settings(stage=3))
    assert report.successful + report.partial >= 1
    assert any((out / "documents").glob("*.json"))


def test_invalid_manifest_aborts(tmp_path: Path) -> None:
    inspection = tmp_path / "insp"
    inspection.mkdir()
    (inspection / "extracted").mkdir()
    (inspection / "manifest.json").write_text("{", encoding="utf-8")
    with pytest.raises(DocumentParsingError):
        parse_inspection_directory(
            inspection, tmp_path / "out", profile="fast", settings=Settings(stage=3)
        )


def test_evidence_catalog_valid_jsonl(tmp_path: Path) -> None:
    inspection = _inspection_with_pdf(tmp_path)
    out = tmp_path / "parsed"
    parse_inspection_directory(inspection, out, profile="fast", settings=Settings(stage=3))
    for line in (out / "evidence_catalog.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            json.loads(line)


def test_generated_documents_reload_through_pydantic(tmp_path: Path) -> None:
    inspection = _inspection_with_pdf(tmp_path)
    out = tmp_path / "parsed"
    parse_inspection_directory(inspection, out, profile="fast", settings=Settings(stage=3))
    for path in (out / "documents").glob("*.json"):
        CanonicalDocument.model_validate_json(path.read_text(encoding="utf-8"))
    ParseBatchReport.model_validate_json((out / "parse_report.json").read_text(encoding="utf-8"))


def test_repeated_fast_runs_equivalent(tmp_path: Path) -> None:
    inspection = _inspection_with_pdf(tmp_path)
    out1 = tmp_path / "p1"
    out2 = tmp_path / "p2"
    parse_inspection_directory(inspection, out1, profile="fast", settings=Settings(stage=3))
    parse_inspection_directory(inspection, out2, profile="fast", settings=Settings(stage=3))
    docs1 = sorted((out1 / "documents").glob("*.json"))
    docs2 = sorted((out2 / "documents").glob("*.json"))
    assert [p.name for p in docs1] == [p.name for p in docs2]
    for a, b in zip(docs1, docs2, strict=True):
        assert a.read_text(encoding="utf-8") == b.read_text(encoding="utf-8")


def test_health_advances_to_stage_3() -> None:
    from fastapi.testclient import TestClient

    from halyk_agent.app.main import create_app
    from halyk_agent.config import Settings
    from halyk_agent.profiles import ProfileName

    client = TestClient(create_app(Settings(profile=ProfileName.FAST, stage=3)))
    assert client.get("/health").json() == {"status": "ok", "stage": 3, "profile": "fast"}
