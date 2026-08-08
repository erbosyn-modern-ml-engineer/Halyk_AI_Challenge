"""Submission-template ambiguity and answer-key quarantine regressions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from halyk_agent.preflight.discover import DatasetAdapterError, discover_and_sanitize


def _template(*, evidence: str | None = None) -> dict[str, object]:
    return {
        "team": "team",
        "contact_email": "team@example.com",
        "model": "model",
        "answers": {"S1": {"6.1": {"status": None, "actual": None, "evidence_txn_id": evidence}}},
    }


def _base(root: Path) -> None:
    (root / "master_ledger_2025.csv").write_text(
        "txn_id,amount,currency\nTXN-S1-0001,1,USD\n", encoding="utf-8"
    )
    (root / "CASE.ru.md").write_text("covenant scenario limit", encoding="utf-8")


def test_evidence_only_answer_key_is_quarantined(tmp_path: Path) -> None:
    _base(tmp_path)
    (tmp_path / "submission_template.json").write_text(json.dumps(_template()), encoding="utf-8")
    (tmp_path / "aaa_answers.json").write_text(
        json.dumps(_template(evidence="TXN-S1-0001")), encoding="utf-8"
    )
    manifest, _inspection = discover_and_sanitize(tmp_path)
    assert Path(manifest.submission_template.path).name == "submission_template.json"
    assert any(Path(item.path).name == "aaa_answers.json" for item in manifest.quarantined)


def test_multiple_root_templates_fail_closed(tmp_path: Path) -> None:
    _base(tmp_path)
    for name in ("a.json", "b.json"):
        (tmp_path / name).write_text(json.dumps(_template()), encoding="utf-8")
    with pytest.raises(DatasetAdapterError, match="ambiguous submission templates"):
        discover_and_sanitize(tmp_path)


def test_root_template_wins_over_nested_decoy(tmp_path: Path) -> None:
    _base(tmp_path)
    (tmp_path / "submission_template.json").write_text(json.dumps(_template()), encoding="utf-8")
    nested = tmp_path / "documents"
    nested.mkdir()
    (nested / "00_old_template.json").write_text(json.dumps(_template()), encoding="utf-8")
    manifest, _inspection = discover_and_sanitize(tmp_path)
    assert Path(manifest.submission_template.path).parent == tmp_path
