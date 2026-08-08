"""Runtime workspace paths/timings must not contaminate routing lineage identity."""

from __future__ import annotations

import json
from pathlib import Path

from halyk_agent.adapters.routing.io import load_ledger_csv_bytes
from halyk_agent.app.authority import _parsed_input_identity as _authority_parsed_input_identity
from halyk_agent.app.routing import _manifest_identity_payload, _parsed_input_identity
from halyk_agent.preflight.models import AllowedInputRef, SanitizedDatasetManifest


def _manifest(root: Path) -> SanitizedDatasetManifest:
    def ref(name: str, role: str) -> AllowedInputRef:
        return AllowedInputRef(
            path=str(root / name),
            sha256=(name.encode().hex() + "0" * 64)[:64],
            size=1,
            role=role,
        )

    return SanitizedDatasetManifest(
        case_descriptions=[ref("CASE.ru.md", "case_description")],
        primary_ledger=ref("master_ledger_2025.csv", "primary_ledger"),
        submission_template=ref("submission_template.json", "submission_template"),
        documents_dir=str(root / "documents"),
        document_files=[ref("documents/a.pdf", "document")],
        technical_noise=[],
        ignored=[],
        quarantined=[],
    )


def test_manifest_identity_ignores_runtime_root(tmp_path: Path) -> None:
    left = _manifest(tmp_path / "run-a")
    right = _manifest(tmp_path / "run-b")
    assert _manifest_identity_payload(left) == _manifest_identity_payload(right)


def test_parsed_input_identity_ignores_parse_durations(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    (left / "parse_report.json").write_text(
        json.dumps({"results": [{"attempts": [{"duration_ms": 1}]}]}), encoding="utf-8"
    )
    (right / "parse_report.json").write_text(
        json.dumps({"results": [{"attempts": [{"duration_ms": 9999}]}]}), encoding="utf-8"
    )
    assert _parsed_input_identity(left, []) == _parsed_input_identity(right, [])


def test_parsed_input_identity_ignores_ocr_paths_timings_and_cache_bytes(tmp_path: Path) -> None:
    left = tmp_path / "left-ocr"
    right = tmp_path / "right-ocr"
    left.mkdir()
    right.mkdir()
    parse_left = {
        "schema_version": "halyk.parse_batch_report.v1",
        "profile": "fast",
        "total_candidates": 1,
        "successful": 1,
        "partial": 0,
        "failed": 0,
        "unsupported": 0,
        "cache_hits": 0,
        "results": [{"attempts": [{"duration_ms": 1}]}],
    }
    parse_right = {
        **parse_left,
        "cache_hits": 1,
        "results": [{"attempts": [{"duration_ms": 9999}]}],
    }
    backend = {
        "kind": "TESSERACT_CLI",
        "backend_version": "tesseract 5.5.0",
        "executable_or_package": "/usr/bin/tesseract",
        "language_data_identity": "lang-hash",
        "languages": ["eng", "rus", "kaz"],
        "render_scale": 2.0,
        "page_segmentation_mode": 6,
        "configuration_hash": "cfg-hash",
    }
    ocr_left = {
        "schema_version": "halyk.selective_ocr_run.v1",
        "backend": backend,
        "selected_pages": 1,
        "attempted_pages": 1,
        "succeeded_pages": 1,
        "failed_pages": 0,
        "remaining_blocking_pages": 0,
        "persistent_cache_bytes_written": 100,
        "documents_processed": 1,
        "offline_ready": True,
        "blocked_reason": None,
        "page_results": [{"request": {"source_path": "/tmp/run-a/doc.pdf"}, "duration_ms": 5}],
    }
    ocr_right = {
        **ocr_left,
        "backend": {**backend, "executable_or_package": "C:/tools/tesseract.exe"},
        "persistent_cache_bytes_written": 999,
        "page_results": [{"request": {"source_path": "C:/run-b/doc.pdf"}, "duration_ms": 5000}],
    }
    for directory, parse_payload, ocr_payload in (
        (left, parse_left, ocr_left),
        (right, parse_right, ocr_right),
    ):
        (directory / "parse_report.json").write_text(json.dumps(parse_payload), encoding="utf-8")
        (directory / "ocr_report.json").write_text(json.dumps(ocr_payload), encoding="utf-8")
        (directory / "evidence_catalog.jsonl").write_text('{"id":"same"}\n', encoding="utf-8")

    assert _parsed_input_identity(left, []) == _parsed_input_identity(right, [])
    assert _authority_parsed_input_identity(left, 1) == _authority_parsed_input_identity(right, 1)


def test_ledger_provenance_uses_stable_basename_across_host_paths() -> None:
    data = (
        b"txn_id,date,account_id,counterparty,description,amount,currency\n"
        b"TXN-P1-0001,2025-01-01,ACC-1,Customer,Revenue,100,USD\n"
    )
    left = load_ledger_csv_bytes(data, source_file="/tmp/run-a/input/master_ledger_2025.csv")
    right = load_ledger_csv_bytes(data, source_file=r"C:\run-b\input\master_ledger_2025.csv")
    assert left == right
    assert left[0].ledger_source_file == "master_ledger_2025.csv"
