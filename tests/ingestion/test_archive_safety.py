"""Archive safety tests."""

from __future__ import annotations

import stat
import zipfile
from pathlib import Path

import pytest

from halyk_agent.adapters.archive.errors import (
    ArchiveLimitExceededError,
    CorruptArchiveError,
    DuplicateArchivePathError,
    UnsafeArchiveEntryError,
)
from halyk_agent.adapters.archive.zip_connector import ArchiveZipConnector
from halyk_agent.config import Settings
from tests.ingestion.helpers import write_zip, write_zip_with_info


def _settings(**overrides: object) -> Settings:
    payload = {
        "max_archive_files": 100,
        "max_single_file_bytes": 1_000_000,
        "max_total_uncompressed_bytes": 5_000_000,
        "max_compression_ratio": 50.0,
        "max_path_length": 200,
        "max_profile_file_bytes": 1_000_000,
        "max_sample_rows": 50,
        "max_sample_value_length": 80,
    }
    payload.update(overrides)
    return Settings.model_validate(payload)


def test_valid_archive_extracts(tmp_path: Path) -> None:
    archive = write_zip(tmp_path / "ok.zip", {"docs/a.txt": "hello", "data/b.csv": "x,y\n1,2\n"})
    result = ArchiveZipConnector(archive, tmp_path / "out", settings=_settings()).extract()
    assert len(result.members) == 2
    assert (tmp_path / "out" / "extracted" / "docs" / "a.txt").read_text(
        encoding="utf-8"
    ) == "hello"


@pytest.mark.parametrize(
    ("member_name", "error_type"),
    [
        ("../escape.txt", UnsafeArchiveEntryError),
        ("/abs/unix.txt", UnsafeArchiveEntryError),
        ("C:/windows/path.txt", UnsafeArchiveEntryError),
        ("\\\\server\\share\\file.txt", UnsafeArchiveEntryError),
    ],
)
def test_unsafe_paths_rejected(
    tmp_path: Path, member_name: str, error_type: type[Exception]
) -> None:
    archive = tmp_path / "bad.zip"
    info = zipfile.ZipInfo(filename=member_name)
    write_zip_with_info(archive, [(info, b"x")])
    with pytest.raises(error_type):
        ArchiveZipConnector(archive, tmp_path / "out", settings=_settings()).extract()


def test_nul_path_rejected() -> None:
    from halyk_agent.adapters.archive.safety import normalize_member_path

    with pytest.raises(UnsafeArchiveEntryError, match="NUL"):
        normalize_member_path("evil\x00name.txt", max_path_length=200)


def test_symlink_entry_rejected(tmp_path: Path) -> None:
    info = zipfile.ZipInfo("link")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    archive = write_zip_with_info(tmp_path / "sym.zip", [(info, b"/tmp/target")])
    with pytest.raises(UnsafeArchiveEntryError, match="symlink"):
        ArchiveZipConnector(archive, tmp_path / "out", settings=_settings()).extract()


def test_duplicate_normalized_path_rejected(tmp_path: Path) -> None:
    archive = write_zip(
        tmp_path / "dup.zip",
        {"dir/../a.txt": "one", "a.txt": "two"},
    )
    # Depending on ZIP creation, both may normalize to a.txt
    with pytest.raises((DuplicateArchivePathError, UnsafeArchiveEntryError)):
        # If path traversal is rejected first for dir/../a.txt, that's also valid safety.
        ArchiveZipConnector(archive, tmp_path / "out", settings=_settings()).extract()


def test_case_insensitive_duplicate_path_rejected(tmp_path: Path) -> None:
    archive = write_zip(tmp_path / "case.zip", {"Docs/A.txt": "one", "docs/a.txt": "two"})
    with pytest.raises(DuplicateArchivePathError):
        ArchiveZipConnector(archive, tmp_path / "out", settings=_settings()).extract()


def test_excessive_file_count_rejected(tmp_path: Path) -> None:
    files = {f"f{i}.txt": "x" for i in range(5)}
    archive = write_zip(tmp_path / "many.zip", files)
    with pytest.raises(ArchiveLimitExceededError):
        ArchiveZipConnector(
            archive, tmp_path / "out", settings=_settings(max_archive_files=3)
        ).extract()


def test_excessive_single_file_size_rejected(tmp_path: Path) -> None:
    archive = write_zip(tmp_path / "big.zip", {"big.txt": "a" * 1000})
    with pytest.raises(ArchiveLimitExceededError):
        ArchiveZipConnector(
            archive, tmp_path / "out", settings=_settings(max_single_file_bytes=100)
        ).extract()


def test_excessive_total_size_rejected(tmp_path: Path) -> None:
    archive = write_zip(
        tmp_path / "total.zip",
        {"a.txt": "a" * 400, "b.txt": "b" * 400},
    )
    with pytest.raises(ArchiveLimitExceededError):
        ArchiveZipConnector(
            archive,
            tmp_path / "out",
            settings=_settings(max_total_uncompressed_bytes=500),
        ).extract()


def test_excessive_compression_ratio_rejected(tmp_path: Path) -> None:
    # Highly compressible payload produces a high compression ratio.
    archive = write_zip(tmp_path / "bomb.zip", {"zeros.bin": "\x00" * 50_000})
    with pytest.raises(ArchiveLimitExceededError):
        ArchiveZipConnector(
            archive,
            tmp_path / "out",
            settings=_settings(max_compression_ratio=2.0, max_single_file_bytes=1_000_000),
        ).extract()


def test_corrupt_zip_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "corrupt.zip"
    archive.write_bytes(b"not-a-zip")
    with pytest.raises(CorruptArchiveError):
        ArchiveZipConnector(archive, tmp_path / "out", settings=_settings()).extract()


def test_partial_temporary_files_are_cleaned(tmp_path: Path) -> None:
    archive = write_zip(tmp_path / "mix.zip", {"ok.txt": "hello", "../bad.txt": "nope"})
    out = tmp_path / "out"
    with pytest.raises(UnsafeArchiveEntryError):
        ArchiveZipConnector(archive, out, settings=_settings()).extract()
    parts = list(out.rglob("*.part"))
    assert parts == []


def test_nested_zip_not_recursively_extracted(tmp_path: Path) -> None:
    nested = write_zip(tmp_path / "nested_inner.zip", {"inner.txt": "secret"})
    archive = write_zip(
        tmp_path / "outer.zip",
        {"outer.txt": "visible", "nested.zip": nested.read_bytes()},
    )
    result = ArchiveZipConnector(archive, tmp_path / "out", settings=_settings()).extract()
    names = {member.normalized_path for member in result.members}
    assert names == {"outer.txt", "nested.zip"}
    assert "inner.txt" not in names
    nested_member = next(
        member for member in result.members if member.normalized_path == "nested.zip"
    )
    assert nested_member.format.value == "ZIP"
