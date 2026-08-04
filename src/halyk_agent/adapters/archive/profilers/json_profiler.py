"""JSON and JSONL schema profilers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from halyk_agent.adapters.archive.profilers.common import build_column_profile
from halyk_agent.config import Settings
from halyk_agent.domain.datasets import ArtifactFormat, TableProfile


def _records_from_json(payload: Any) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    if isinstance(payload, list):
        records = [item for item in payload if isinstance(item, dict)]
        if len(records) != len(payload):
            warnings.append("JSON array contained non-object elements that were skipped")
        return records, warnings
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
                warnings.append(f"using nested record list under key {key!r}")
                return list(value), warnings
        warnings.append("top-level JSON object treated as a single record")
        return [payload], warnings
    warnings.append("unsupported JSON root type")
    return [], warnings


def _flatten_for_profile(record: dict[str, Any]) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in record.items():
        if isinstance(value, (dict, list)):
            flat[key] = json.dumps(value, ensure_ascii=False, sort_keys=True)
        else:
            flat[key] = value
    return flat


def profile_json(path: Path, *, artifact_id: str, settings: Settings) -> TableProfile:
    """Profile a JSON document with bounded loading."""
    size = path.stat().st_size
    if size > settings.max_profile_file_bytes:
        return TableProfile(
            artifact_id=artifact_id,
            format=ArtifactFormat.JSON,
            sampled_rows=0,
            warnings=[
                f"JSON larger than max_profile_file_bytes={settings.max_profile_file_bytes}; "
                "skipped deep profiling"
            ],
        )
    warnings: list[str] = []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return TableProfile(
            artifact_id=artifact_id,
            format=ArtifactFormat.JSON,
            sampled_rows=0,
            warnings=[f"malformed JSON: {exc}"],
        )
    records, record_warnings = _records_from_json(payload)
    warnings.extend(record_warnings)
    return _profile_records(
        records,
        artifact_id=artifact_id,
        format_=ArtifactFormat.JSON,
        settings=settings,
        warnings=warnings,
    )


def profile_jsonl(path: Path, *, artifact_id: str, settings: Settings) -> TableProfile:
    """Profile a JSON Lines file with bounded sampling."""
    size = path.stat().st_size
    if size > settings.max_profile_file_bytes:
        return TableProfile(
            artifact_id=artifact_id,
            format=ArtifactFormat.JSONL,
            sampled_rows=0,
            warnings=[
                f"JSONL larger than max_profile_file_bytes={settings.max_profile_file_bytes}; "
                "skipped deep profiling"
            ],
        )
    warnings: list[str] = []
    records: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                if len(records) >= settings.max_sample_rows:
                    break
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    payload = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    warnings.append(f"malformed JSONL at line {line_no}: {exc}")
                    continue
                if isinstance(payload, dict):
                    records.append(payload)
                else:
                    warnings.append(f"non-object JSONL record at line {line_no}")
    except (OSError, UnicodeDecodeError) as exc:
        return TableProfile(
            artifact_id=artifact_id,
            format=ArtifactFormat.JSONL,
            sampled_rows=0,
            warnings=[f"failed reading JSONL: {exc}"],
        )
    return _profile_records(
        records,
        artifact_id=artifact_id,
        format_=ArtifactFormat.JSONL,
        settings=settings,
        warnings=warnings,
    )


def _profile_records(
    records: list[dict[str, Any]],
    *,
    artifact_id: str,
    format_: ArtifactFormat,
    settings: Settings,
    warnings: list[str],
) -> TableProfile:
    sample = [_flatten_for_profile(record) for record in records[: settings.max_sample_rows]]
    nested = False
    for record in records[: settings.max_sample_rows]:
        for value in record.values():
            if isinstance(value, (dict, list)):
                nested = True
                break
        if nested:
            break
    if nested:
        warnings.append("nested JSON values serialized as strings for profiling")

    keys: list[str] = []
    seen: set[str] = set()
    for record in sample:
        for key in record:
            if key not in seen:
                seen.add(key)
                keys.append(key)
    columns = [
        build_column_profile(
            name=key,
            position=position,
            values=[record.get(key) for record in sample],
            max_sample_value_length=settings.max_sample_value_length,
        )
        for position, key in enumerate(keys)
    ]
    return TableProfile(
        artifact_id=artifact_id,
        format=format_,
        encoding="utf-8",
        header_detected=True,
        sampled_rows=len(sample),
        columns=columns,
        warnings=list(dict.fromkeys(warnings)),
    )
