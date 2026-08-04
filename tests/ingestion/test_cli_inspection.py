"""CLI and application inspection tests."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from halyk_agent.app.cli import main
from halyk_agent.app.main import create_app
from halyk_agent.config import Settings
from halyk_agent.domain.datasets import DatasetManifest, SchemaProfileDocument
from halyk_agent.profiles import ProfileName
from tests.ingestion.helpers import sample_transactions_csv, write_zip


def test_cli_outputs_overwrite_and_unsafe(tmp_path: Path) -> None:
    archive = write_zip(tmp_path / "data.zip", {"transactions.csv": sample_transactions_csv()})
    out = tmp_path / "out"
    assert main(["inspect", "--input", str(archive), "--output", str(out)]) == 0
    assert (out / "manifest.json").is_file()
    assert (out / "schema_profile.json").is_file()
    assert (out / "inspection_summary.md").is_file()

    DatasetManifest.model_validate(json.loads((out / "manifest.json").read_text(encoding="utf-8")))
    SchemaProfileDocument.model_validate(
        json.loads((out / "schema_profile.json").read_text(encoding="utf-8"))
    )

    assert main(["inspect", "--input", str(archive), "--output", str(out)]) == 1
    assert main(["inspect", "--input", str(archive), "--output", str(out), "--overwrite"]) == 0

    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"nope")
    assert main(["inspect", "--input", str(bad), "--output", str(tmp_path / "bad-out")]) == 1


def test_health_remains_valid_for_stage_2() -> None:
    settings = Settings(profile=ProfileName.FAST, stage=2, app_name="halyk-agent")
    client = TestClient(create_app(settings))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "stage": 2, "profile": "fast"}
