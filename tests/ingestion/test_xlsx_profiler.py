"""XLSX profiler tests."""

from __future__ import annotations

from pathlib import Path

from halyk_agent.adapters.archive.hashing import sha256_file
from halyk_agent.adapters.archive.profilers.xlsx_profiler import profile_xlsx
from halyk_agent.config import Settings
from tests.ingestion.helpers import make_xlsx_bytes


def _settings() -> Settings:
    return Settings(
        max_sample_rows=50, max_sample_value_length=80, max_profile_file_bytes=2_000_000
    )


def test_xlsx_sheets_header_formulas_and_hash_stability(tmp_path: Path) -> None:
    payload = make_xlsx_bytes(
        {
            "Transactions": [
                ["transaction_id", "amount", "currency"],
                ["t1", 10, "KZT"],
                ["t2", "=A2", "KZT"],
            ],
            "Empty": [],
        }
    )
    path = tmp_path / "book.xlsx"
    path.write_bytes(payload)
    before = sha256_file(path)
    profile = profile_xlsx(path, artifact_id="book", settings=_settings())
    after = sha256_file(path)
    assert before == after
    assert len(profile.sheets) == 2
    tx = next(sheet for sheet in profile.sheets if sheet.name == "Transactions")
    assert tx.columns[0].normalized_name == "transaction_id"
    assert tx.formula_cell_count >= 1
    empty = next(sheet for sheet in profile.sheets if sheet.name == "Empty")
    assert empty.sampled_rows == 0
