"""CSV schema profiler."""

from __future__ import annotations

import csv
from pathlib import Path

from charset_normalizer import from_bytes

from halyk_agent.adapters.archive.profilers.common import build_column_profile
from halyk_agent.config import Settings
from halyk_agent.domain.datasets import ArtifactFormat, TableProfile


def _detect_encoding(raw: bytes) -> tuple[str, list[str]]:
    warnings: list[str] = []
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig", warnings
    try:
        raw.decode("utf-8")
        return "utf-8", warnings
    except UnicodeDecodeError:
        pass
    result = from_bytes(raw).best()
    if result is None:
        warnings.append("charset detection failed; falling back to latin-1")
        return "latin-1", warnings
    encoding = result.encoding or "latin-1"
    warnings.append(f"decoded CSV using charset-normalizer encoding={encoding}")
    return encoding, warnings


def _detect_delimiter(sample: str) -> tuple[str, list[str]]:
    warnings: list[str] = []
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","
        warnings.append("csv.Sniffer failed; defaulting to comma delimiter")
    # Prefer majority vote among common delimiters when sniffer is uncertain.
    counts = {d: sample.count(d) for d in [",", ";", "\t", "|"]}
    majority = max(counts, key=lambda key: counts[key])
    if (
        counts[majority] > 0
        and majority != delimiter
        and counts[majority] >= counts.get(delimiter, 0)
    ):
        warnings.append(
            f"delimiter majority vote preferred {majority!r} over sniffer {delimiter!r}"
        )
        delimiter = majority
    return delimiter, warnings


def profile_csv(path: Path, *, artifact_id: str, settings: Settings) -> TableProfile:
    """Profile a CSV file using bounded sampling."""
    warnings: list[str] = []
    size = path.stat().st_size
    if size > settings.max_profile_file_bytes:
        return TableProfile(
            artifact_id=artifact_id,
            format=ArtifactFormat.CSV,
            sampled_rows=0,
            warnings=[
                f"CSV larger than max_profile_file_bytes={settings.max_profile_file_bytes}; "
                "skipped deep profiling"
            ],
        )

    raw = path.read_bytes()
    if not raw.strip():
        return TableProfile(
            artifact_id=artifact_id,
            format=ArtifactFormat.CSV,
            encoding="utf-8",
            header_detected=False,
            sampled_rows=0,
            warnings=["empty CSV file"],
        )

    encoding, enc_warnings = _detect_encoding(raw)
    warnings.extend(enc_warnings)
    text = raw.decode(encoding, errors="replace")
    sample = text[:8192]
    delimiter, delim_warnings = _detect_delimiter(sample)
    warnings.extend(delim_warnings)

    reader = csv.reader(text.splitlines(), delimiter=delimiter)
    rows = []
    for index, row in enumerate(reader):
        rows.append(row)
        if index + 1 >= settings.max_sample_rows + 1:
            break
    if not rows:
        return TableProfile(
            artifact_id=artifact_id,
            format=ArtifactFormat.CSV,
            encoding=encoding,
            delimiter=delimiter,
            header_detected=False,
            sampled_rows=0,
            warnings=["CSV contained no rows"],
        )

    header = rows[0]
    data_rows = rows[1:] if len(rows) > 1 else []
    header_detected = any(cell.strip() for cell in header)
    if not header_detected:
        header = [f"column_{i}" for i in range(len(header))]
        data_rows = rows
        warnings.append("header not detected; synthesized column names")

    seen: dict[str, int] = {}
    headers: list[str] = []
    for cell in header:
        name = cell.strip() or "unnamed"
        if name in seen:
            seen[name] += 1
            warnings.append(f"duplicate CSV header {name!r}")
            headers.append(f"{name}__{seen[name]}")
        else:
            seen[name] = 0
            headers.append(name)

    width = len(headers)
    columns_values: list[list[str | None]] = [[] for _ in range(width)]
    for row in data_rows:
        if len(row) != width:
            warnings.append(f"uneven CSV row length {len(row)} (expected {width})")
        for idx in range(width):
            value = row[idx] if idx < len(row) else None
            columns_values[idx].append(value)

    columns = [
        build_column_profile(
            name=headers[idx],
            position=idx,
            values=columns_values[idx],
            max_sample_value_length=settings.max_sample_value_length,
        )
        for idx in range(width)
    ]
    # Deduplicate warnings while preserving order.
    ordered_warnings = list(dict.fromkeys(warnings))
    return TableProfile(
        artifact_id=artifact_id,
        format=ArtifactFormat.CSV,
        encoding=encoding,
        delimiter=delimiter,
        header_detected=header_detected,
        sampled_rows=len(data_rows),
        columns=columns,
        warnings=ordered_warnings,
    )
