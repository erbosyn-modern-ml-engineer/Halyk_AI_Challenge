"""Role classification tests."""

from __future__ import annotations

from pathlib import Path

from halyk_agent.app.inspection import inspect_archive
from halyk_agent.config import Settings
from halyk_agent.domain.datasets import ArtifactRole
from tests.ingestion.helpers import (
    sample_scoring_json,
    sample_submission_json,
    sample_transactions_csv,
    write_zip,
)


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


def test_role_classification_signals(tmp_path: Path) -> None:
    archive = write_zip(
        tmp_path / "roles.zip",
        {
            "transactions.csv": sample_transactions_csv(),
            "submission_template.json": sample_submission_json(),
            "scoring_rules.json": sample_scoring_json(),
            "notes/random.bin": b"\x00\x01\x02",
        },
    )
    result = inspect_archive(archive, tmp_path / "out", overwrite=True, settings=_settings())
    by_path = {item.normalized_path: item for item in result.manifest.artifacts}

    txn = by_path["transactions.csv"]
    assert txn.role is ArtifactRole.TRANSACTION_TABLE
    assert 0.0 <= txn.role_confidence <= 1.0
    assert txn.role_reasons

    submission = by_path["submission_template.json"]
    assert submission.role is ArtifactRole.SUBMISSION_TEMPLATE
    assert submission.role_reasons

    scoring = by_path["scoring_rules.json"]
    assert scoring.role is ArtifactRole.SCORING_RULES
    assert scoring.role_reasons

    weak = by_path["notes/random.bin"]
    assert weak.role in {ArtifactRole.UNKNOWN, ArtifactRole.DOCUMENT, ArtifactRole.METADATA}
    if weak.role is ArtifactRole.UNKNOWN:
        assert weak.role_confidence <= 0.5
