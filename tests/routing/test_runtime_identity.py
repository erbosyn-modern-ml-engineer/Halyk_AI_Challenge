"""Runtime workspace paths/timings must not contaminate routing lineage identity."""

from __future__ import annotations

import json
from pathlib import Path

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
