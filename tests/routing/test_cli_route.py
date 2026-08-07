"""CLI route command smoke tests."""

from __future__ import annotations

import json
from pathlib import Path

from halyk_agent.app.cli import main


def test_route_cli_writes_manifest(tmp_path: Path) -> None:
    # Minimal sanitized manifest pointing at temp template/ledger.
    template = {
        "team": "t",
        "contact_email": "a@b.c",
        "model": "m",
        "answers": {"P1": {"6.1": {"status": None, "actual": None, "evidence_txn_id": None}}},
    }
    template_path = tmp_path / "submission_template.json"
    template_path.write_text(json.dumps(template), encoding="utf-8")
    ledger_path = tmp_path / "ledger.csv"
    ledger_path.write_text(
        "txn_id,date,account_id,counterparty,description,amount,currency\n"
        "TXN-P1-0001,2025-01-01,ACC-7801,Alpha,pay,1.00,KZT\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "halyk.sanitized_dataset_manifest.v1",
        "case_descriptions": [],
        "primary_ledger": {
            "path": ledger_path.as_posix(),
            "sha256": "a" * 64,
            "size": 1,
            "role": "primary_ledger",
        },
        "submission_template": {
            "path": template_path.as_posix(),
            "sha256": "b" * 64,
            "size": 1,
            "role": "submission_template",
        },
        "document_files": [],
        "technical_noise": [],
        "ignored": [],
        "quarantined": [],
    }
    manifest_path = tmp_path / "sanitized_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    parsed = tmp_path / "parsed"
    (parsed / "documents").mkdir(parents=True)
    # Minimal parse report with no documents.
    parse_report = {
        "schema_version": "halyk.parse_batch_report.v1",
        "profile": "full",
        "total_candidates": 0,
        "successful": 0,
        "partial": 0,
        "failed": 0,
        "unsupported": 0,
        "cache_hits": 0,
        "results": [],
    }
    (parsed / "parse_report.json").write_text(json.dumps(parse_report), encoding="utf-8")

    out = tmp_path / "routing"
    code = main(
        [
            "route",
            "--dataset-manifest",
            str(manifest_path),
            "--parsed",
            str(parsed),
            "--output",
            str(out),
            "--overwrite",
        ]
    )
    assert code == 0
    assert (out / "routing_manifest.json").is_file()
    payload = json.loads((out / "routing_manifest.json").read_text(encoding="utf-8"))
    assert payload["scenario_count"] == 1
    assert payload["scenario_transaction_count"] == 1
