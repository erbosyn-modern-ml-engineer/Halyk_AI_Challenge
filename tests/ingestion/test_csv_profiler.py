"""CSV profiler tests."""

from __future__ import annotations

from pathlib import Path

from halyk_agent.adapters.archive.profilers.csv_profiler import profile_csv
from halyk_agent.config import Settings
from halyk_agent.domain.datasets import PrimitiveType


def _settings() -> Settings:
    return Settings(
        max_sample_rows=50, max_sample_value_length=80, max_profile_file_bytes=1_000_000
    )


def test_comma_semicolon_tab_csv(tmp_path: Path) -> None:
    cases = {
        "comma.csv": "transaction_id,amount\nt1,1.5\n",
        "semi.csv": "transaction_id;amount\nt1;1.5\n",
        "tab.csv": "transaction_id\tamount\nt1\t1.5\n",
    }
    for name, content in cases.items():
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        profile = profile_csv(path, artifact_id=name, settings=_settings())
        assert profile.delimiter in {",", ";", "\t"}
        assert profile.columns[0].name.startswith("transaction")
        assert profile.sampled_rows == 1


def test_utf8_bom_and_latin1_fallback(tmp_path: Path) -> None:
    bom = tmp_path / "bom.csv"
    bom.write_bytes("\ufeffid,name\n1,café\n".encode("utf-8-sig"))
    profile = profile_csv(bom, artifact_id="bom", settings=_settings())
    assert profile.encoding in {"utf-8-sig", "utf-8"}

    latin = tmp_path / "latin.csv"
    latin.write_bytes(b"id;name\n1;caf\xe9\n")
    profile_latin = profile_csv(latin, artifact_id="latin", settings=_settings())
    assert profile_latin.encoding is not None
    assert profile_latin.sampled_rows >= 1


def test_empty_uneven_duplicate_and_types(tmp_path: Path) -> None:
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    empty_profile = profile_csv(empty, artifact_id="empty", settings=_settings())
    assert "empty CSV file" in empty_profile.warnings

    uneven = tmp_path / "uneven.csv"
    uneven.write_text("a,b\n1,2,3\n", encoding="utf-8")
    uneven_profile = profile_csv(uneven, artifact_id="uneven", settings=_settings())
    assert any("uneven" in warning for warning in uneven_profile.warnings)

    dup = tmp_path / "dup.csv"
    dup.write_text("id,id\n1,2\n", encoding="utf-8")
    dup_profile = profile_csv(dup, artifact_id="dup", settings=_settings())
    assert any("duplicate" in warning for warning in dup_profile.warnings)

    typed = tmp_path / "typed.csv"
    typed.write_text("amount,flag,when\n10.5,true,2024-01-01\n", encoding="utf-8")
    typed_profile = profile_csv(typed, artifact_id="typed", settings=_settings())
    by_name = {col.normalized_name: col.primitive_type for col in typed_profile.columns}
    assert by_name["amount"] in {PrimitiveType.DECIMAL, PrimitiveType.INTEGER}
    assert by_name["flag"] is PrimitiveType.BOOLEAN
    assert by_name["when"] in {PrimitiveType.DATE, PrimitiveType.DATETIME, PrimitiveType.STRING}
