"""Hashing and manifest determinism tests."""

from __future__ import annotations

import json
from pathlib import Path

from halyk_agent.adapters.archive.hashing import artifact_id_for, sha256_file
from halyk_agent.app.inspection import inspect_archive
from halyk_agent.config import Settings
from halyk_agent.domain.datasets import DatasetManifest
from tests.ingestion.helpers import sample_transactions_csv, write_zip


def _settings() -> Settings:
    return Settings(
        max_archive_files=100,
        max_single_file_bytes=1_000_000,
        max_total_uncompressed_bytes=5_000_000,
        max_compression_ratio=100.0,
        max_path_length=200,
        max_profile_file_bytes=1_000_000,
        max_sample_rows=50,
        max_sample_value_length=80,
    )


def test_archive_and_file_hashes_stable(tmp_path: Path) -> None:
    archive = write_zip(tmp_path / "d.zip", {"a.txt": "same"})
    assert sha256_file(archive) == sha256_file(archive)
    extracted = tmp_path / "a.txt"
    extracted.write_text("same", encoding="utf-8")
    assert sha256_file(extracted) == sha256_file(extracted)


def test_artifact_id_stable() -> None:
    assert artifact_id_for("a.txt", "abc") == artifact_id_for("a.txt", "abc")
    assert artifact_id_for("a.txt", "abc") != artifact_id_for("b.txt", "abc")


def test_manifest_ordering_and_repeatability(tmp_path: Path) -> None:
    archive = write_zip(
        tmp_path / "data.zip",
        {
            "z_last.csv": "a,b\n1,2\n",
            "a_first.csv": sample_transactions_csv(),
            "m_mid.txt": "note",
        },
    )
    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"
    result1 = inspect_archive(archive, out1, overwrite=True, settings=_settings())
    result2 = inspect_archive(archive, out2, overwrite=True, settings=_settings())
    paths1 = [item.normalized_path for item in result1.manifest.artifacts]
    paths2 = [item.normalized_path for item in result2.manifest.artifacts]
    assert paths1 == sorted(paths1)
    assert paths1 == paths2
    assert result1.manifest.archive_sha256 == result2.manifest.archive_sha256
    raw1 = (out1 / "manifest.json").read_text(encoding="utf-8")
    raw2 = (out2 / "manifest.json").read_text(encoding="utf-8")
    assert raw1 == raw2
    DatasetManifest.model_validate(json.loads(raw1))
