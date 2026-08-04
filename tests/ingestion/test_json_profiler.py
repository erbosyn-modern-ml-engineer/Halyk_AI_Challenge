"""JSON/JSONL profiler tests."""

from __future__ import annotations

from pathlib import Path

from halyk_agent.adapters.archive.profilers.json_profiler import profile_json, profile_jsonl
from halyk_agent.config import Settings


def _settings(**overrides: object) -> Settings:
    base = {
        "max_sample_rows": 50,
        "max_sample_value_length": 80,
        "max_profile_file_bytes": 1_000_000,
    }
    base.update(overrides)
    return Settings.model_validate(base)


def test_json_array_and_object_with_records(tmp_path: Path) -> None:
    array_path = tmp_path / "arr.json"
    array_path.write_text(
        '[{"transaction_id":"t1","amount":1},{"transaction_id":"t2","amount":2}]', encoding="utf-8"
    )
    array_profile = profile_json(array_path, artifact_id="arr", settings=_settings())
    assert array_profile.sampled_rows == 2
    assert any(col.normalized_name == "transaction_id" for col in array_profile.columns)

    obj_path = tmp_path / "obj.json"
    obj_path.write_text(
        '{"records":[{"case_id":"c1","question":"q"},{"case_id":"c2","question":"q2"}]}',
        encoding="utf-8",
    )
    obj_profile = profile_json(obj_path, artifact_id="obj", settings=_settings())
    assert obj_profile.sampled_rows == 2
    assert any("nested record list" in warning for warning in obj_profile.warnings)


def test_jsonl_malformed_and_oversized(tmp_path: Path) -> None:
    jsonl = tmp_path / "rows.jsonl"
    jsonl.write_text('{"id":1}\nNOT_JSON\n{"id":2}\n', encoding="utf-8")
    profile = profile_jsonl(jsonl, artifact_id="jsonl", settings=_settings())
    assert profile.sampled_rows == 2
    assert any("malformed JSONL" in warning for warning in profile.warnings)

    big = tmp_path / "big.json"
    big.write_text('{"a":1}', encoding="utf-8")
    oversized = profile_json(big, artifact_id="big", settings=_settings(max_profile_file_bytes=1))
    assert any("skipped deep profiling" in warning for warning in oversized.warnings)
