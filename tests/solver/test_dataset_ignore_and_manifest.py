"""Dataset adapter ignore rules and determinism."""

from __future__ import annotations

import json
from pathlib import Path

from halyk_agent.solver.audit import RunFileAudit
from halyk_agent.solver.dataset.adapter import discover_dataset
from halyk_agent.solver.dataset.ignore import classify_ignore


def _mini_dataset(root: Path) -> None:
    (root / "CASE.ru.md").write_text(
        "# Case\n\nCovenant limit scenario details.\n", encoding="utf-8"
    )
    (root / "CASE.kz.md").write_text("# Case\n\nCovenant лимит scenario.\n", encoding="utf-8")
    (root / "master_ledger_2025.csv").write_text(
        "txn_id,date,account_id,counterparty,description,amount,currency\n"
        "T1,2025-01-01,A1,C1,desc,100,KZT\n",
        encoding="utf-8",
    )
    template = {
        "team": "t",
        "contact_email": "a@b.c",
        "model": "baseline",
        "answers": {"P1": {"6.1": {"status": None, "actual": None, "evidence_txn_id": None}}},
    }
    (root / "submission_template.json").write_text(
        json.dumps(template, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    docs = root / "documents"
    docs.mkdir()
    (docs / "doc.pdf").write_bytes(b"%PDF-1.4 mini")
    (docs / "noise.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (docs / "tiny.db").write_bytes(b"SQLite")
    macos = root / "__MACOSX" / "agentic"
    macos.mkdir(parents=True)
    (macos / "._CASE.ru.md").write_bytes(b"\x00\x05\x16\x07junk")
    (root / ".DS_Store").write_bytes(b"store")


def test_ignore_rules_macos_appledouble_and_tiny(tmp_path: Path) -> None:
    _mini_dataset(tmp_path)
    assert classify_ignore(tmp_path / "__MACOSX" / "agentic" / "._CASE.ru.md") is not None
    assert classify_ignore(tmp_path / ".DS_Store") is not None
    assert classify_ignore(tmp_path / "documents" / "tiny.db") is not None
    manifest = discover_dataset(tmp_path, audit=RunFileAudit(run_id="t"))
    assert any(
        item.ignore_rule.startswith("path_component") or "appledouble" in item.ignore_rule
        for item in manifest.ignored
    )
    assert manifest.primary_ledger is not None
    assert manifest.submission_template is not None
    noise_paths = {Path(item.path).name for item in manifest.technical_noise}
    assert "noise.csv" in noise_paths
    assert "tiny.db" not in {Path(p.path).name for p in manifest.document_files}


def test_manifest_deterministic(tmp_path: Path) -> None:
    _mini_dataset(tmp_path)
    a = discover_dataset(tmp_path).model_dump(mode="json")
    b = discover_dataset(tmp_path).model_dump(mode="json")
    assert a == b


def test_dataset_files_unchanged_after_discover(tmp_path: Path) -> None:
    _mini_dataset(tmp_path)
    before = {
        str(p.as_posix()): (p.stat().st_size, p.read_bytes())
        for p in sorted(tmp_path.rglob("*"))
        if p.is_file()
    }
    discover_dataset(tmp_path)
    after = {
        str(p.as_posix()): (p.stat().st_size, p.read_bytes())
        for p in sorted(tmp_path.rglob("*"))
        if p.is_file()
    }
    assert before == after
