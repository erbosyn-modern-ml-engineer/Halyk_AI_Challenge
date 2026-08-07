"""Stage 5B.1 HIGH-5: routing sanitized/audited dataset access regressions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from halyk_agent.app.routing import route_from_paths
from halyk_agent.dataset_access import LeakageAttemptError
from halyk_agent.preflight.models import (
    AllowedInputRef,
    JsonCandidateRole,
    QuarantinedRef,
    SanitizedDatasetManifest,
)


def _write_template(path: Path) -> None:
    payload = {
        "team": "t",
        "contact_email": "a@b.c",
        "model": "m",
        "answers": {"P1": {"6.1": {"status": None, "actual": None, "evidence_txn_id": None}}},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_ledger(path: Path) -> None:
    path.write_text(
        "txn_id,date,account_id,counterparty,description,amount,currency\n"
        "TXN-P1-0001,2025-01-01,ACC-7801,Alpha,pay,1.00,KZT\n",
        encoding="utf-8",
    )


def _parsed_stub(root: Path) -> Path:
    parsed = root / "parsed"
    (parsed / "documents").mkdir(parents=True)
    report = {
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
    (parsed / "parse_report.json").write_text(json.dumps(report), encoding="utf-8")
    return parsed


def _manifest(
    *,
    template: Path,
    ledger: Path,
    quarantined: list[Path] | None = None,
) -> SanitizedDatasetManifest:
    qrefs = [
        QuarantinedRef(
            path=path.as_posix(),
            sha256="c" * 64,
            size=1,
            role=JsonCandidateRole.QUARANTINED_ANSWER_KEY,
            quarantine_reason="test",
        )
        for path in (quarantined or [])
    ]
    return SanitizedDatasetManifest(
        primary_ledger=AllowedInputRef(
            path=ledger.as_posix(),
            sha256="a" * 64,
            size=1,
            role="primary_ledger",
        ),
        submission_template=AllowedInputRef(
            path=template.as_posix(),
            sha256="b" * 64,
            size=1,
            role="submission_template",
        ),
        quarantined=qrefs,
    )


def test_valid_sanitized_manifest_routes(tmp_path: Path) -> None:
    template = tmp_path / "submission_template.json"
    ledger = tmp_path / "ledger.csv"
    _write_template(template)
    _write_ledger(ledger)
    manifest = _manifest(template=template, ledger=ledger)
    manifest_path = tmp_path / "sanitized_manifest.json"
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
    out = tmp_path / "out"
    report = route_from_paths(
        dataset_manifest=manifest_path,
        parsed_dir=_parsed_stub(tmp_path),
        output_dir=out,
        overwrite=True,
    )
    assert report.manifest.scenario_count == 1
    assert (out / "routing_manifest.json").is_file()
    assert (out / "routing_open_audit.json").is_file()
    audit = json.loads((out / "routing_open_audit.json").read_text(encoding="utf-8"))
    names = {Path(item["path"]).name for item in audit["opened_files"]}
    assert "submission_template.json" in names
    assert "ledger.csv" in names
    assert "ground_truth.json" not in names


def test_ground_truth_in_template_role_rejected(tmp_path: Path) -> None:
    gt = tmp_path / "ground_truth.json"
    gt.write_text('{"scenarios":{}}', encoding="utf-8")
    ledger = tmp_path / "ledger.csv"
    _write_ledger(ledger)
    # Also quarantine it.
    manifest = _manifest(template=gt, ledger=ledger, quarantined=[gt])
    manifest_path = tmp_path / "sanitized_manifest.json"
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
    out = tmp_path / "out-fail"
    out.mkdir()
    with pytest.raises(LeakageAttemptError):
        route_from_paths(
            dataset_manifest=manifest_path,
            parsed_dir=_parsed_stub(tmp_path),
            output_dir=out,
            overwrite=True,
        )
    assert list(out.iterdir()) == [] or not any(
        p.name.startswith("routing_") for p in out.iterdir()
    )


def test_renamed_answer_key_in_template_role_rejected(tmp_path: Path) -> None:
    secret = tmp_path / "answers_secret.json"
    secret.write_text('{"scenarios":{"P1":{"covenants":{}}}}', encoding="utf-8")
    ledger = tmp_path / "ledger.csv"
    _write_ledger(ledger)
    manifest = _manifest(template=secret, ledger=ledger, quarantined=[secret])
    manifest_path = tmp_path / "sanitized_manifest.json"
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
    out = tmp_path / "out-renamed"
    with pytest.raises(LeakageAttemptError):
        route_from_paths(
            dataset_manifest=manifest_path,
            parsed_dir=_parsed_stub(tmp_path),
            output_dir=out,
            overwrite=True,
        )
    assert not out.exists() or not any(out.glob("routing_manifest.json"))


def test_path_alias_to_quarantine_rejected(tmp_path: Path) -> None:
    real = tmp_path / "answers_secret.json"
    real.write_text("{}", encoding="utf-8")
    ledger = tmp_path / "ledger.csv"
    _write_ledger(ledger)
    # Relative alias that resolves to the quarantined file.
    alias = Path(real.name)
    # Store absolute quarantine + relative allowlist role that resolves same.
    manifest = SanitizedDatasetManifest(
        primary_ledger=AllowedInputRef(
            path=ledger.resolve().as_posix(),
            sha256="a" * 64,
            size=1,
            role="primary_ledger",
        ),
        submission_template=AllowedInputRef(
            path=(tmp_path / alias).as_posix(),
            sha256="b" * 64,
            size=1,
            role="submission_template",
        ),
        quarantined=[
            QuarantinedRef(
                path=real.resolve().as_posix(),
                sha256="c" * 64,
                size=1,
                role=JsonCandidateRole.QUARANTINED_ANSWER_KEY,
                quarantine_reason="test",
            )
        ],
    )
    manifest_path = tmp_path / "sanitized_manifest.json"
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
    with pytest.raises(LeakageAttemptError):
        route_from_paths(
            dataset_manifest=manifest_path,
            parsed_dir=_parsed_stub(tmp_path),
            output_dir=tmp_path / "out-alias",
            overwrite=True,
        )


def test_windows_separator_alias_rejected(tmp_path: Path) -> None:
    secret = tmp_path / "answers_secret.json"
    secret.write_text("{}", encoding="utf-8")
    ledger = tmp_path / "ledger.csv"
    _write_ledger(ledger)
    mixed = str(secret.resolve()).replace("/", "\\")
    manifest = _manifest(template=Path(mixed), ledger=ledger, quarantined=[secret.resolve()])
    manifest_path = tmp_path / "sanitized_manifest.json"
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
    with pytest.raises(LeakageAttemptError):
        route_from_paths(
            dataset_manifest=manifest_path,
            parsed_dir=_parsed_stub(tmp_path),
            output_dir=tmp_path / "out-win",
            overwrite=True,
        )


def test_unaudited_opener_rejected(tmp_path: Path) -> None:
    template = tmp_path / "submission_template.json"
    ledger = tmp_path / "ledger.csv"
    _write_template(template)
    _write_ledger(ledger)
    manifest = _manifest(template=template, ledger=ledger)
    manifest_path = tmp_path / "sanitized_manifest.json"
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")

    class BadOpener:
        def read_bytes(self, path: Path) -> bytes:
            return path.read_bytes()

    with pytest.raises(LeakageAttemptError, match="opened_paths"):
        route_from_paths(
            dataset_manifest=manifest_path,
            parsed_dir=_parsed_stub(tmp_path),
            output_dir=tmp_path / "out-bad",
            overwrite=True,
            opener=BadOpener(),  # type: ignore[arg-type]
        )
