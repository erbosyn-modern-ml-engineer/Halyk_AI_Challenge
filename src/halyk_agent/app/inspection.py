"""Archive inspection application service."""

from __future__ import annotations

import json
import os
from pathlib import Path

from halyk_agent.adapters.archive.classifier import classify_role
from halyk_agent.adapters.archive.errors import ArchiveInspectionError
from halyk_agent.adapters.archive.hashing import artifact_id_for
from halyk_agent.adapters.archive.profilers import (
    profile_csv,
    profile_json,
    profile_jsonl,
    profile_xlsx,
)
from halyk_agent.adapters.archive.zip_connector import ArchiveZipConnector, ExtractionResult
from halyk_agent.config import Settings, get_settings
from halyk_agent.domain.datasets import (
    ArtifactFormat,
    DatasetArtifact,
    DatasetManifest,
    InspectionResult,
    SchemaProfileDocument,
    TableProfile,
)


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _dump_json(model: DatasetManifest | SchemaProfileDocument) -> str:
    payload = model.model_dump(mode="json")
    return json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def _profile_member(
    *,
    artifact_id: str,
    path: Path,
    format_: ArtifactFormat,
    settings: Settings,
) -> TableProfile | None:
    if format_ is ArtifactFormat.CSV:
        return profile_csv(path, artifact_id=artifact_id, settings=settings)
    if format_ is ArtifactFormat.JSON:
        return profile_json(path, artifact_id=artifact_id, settings=settings)
    if format_ is ArtifactFormat.JSONL:
        return profile_jsonl(path, artifact_id=artifact_id, settings=settings)
    if format_ is ArtifactFormat.XLSX:
        return profile_xlsx(path, artifact_id=artifact_id, settings=settings)
    return None


def _build_summary_markdown(
    manifest: DatasetManifest,
    schema_profile: SchemaProfileDocument,
) -> str:
    lines = [
        "# Archive inspection summary",
        "",
        f"- Archive: `{manifest.archive_name}`",
        f"- Archive SHA-256: `{manifest.archive_sha256}`",
        f"- Files: {manifest.total_files}",
        f"- Total uncompressed bytes: {manifest.total_uncompressed_bytes}",
        f"- Profiled tables: {len(schema_profile.tables)}",
        "",
        "## Artifacts",
        "",
    ]
    for artifact in manifest.artifacts:
        lines.append(
            f"- `{artifact.normalized_path}` — {artifact.format.value} / "
            f"{artifact.role.value} ({artifact.role_confidence:.2f})"
        )
        if artifact.role_reasons:
            lines.append(f"  - reasons: {'; '.join(artifact.role_reasons)}")
        if artifact.warnings:
            lines.append(f"  - warnings: {'; '.join(artifact.warnings)}")
    if manifest.warnings:
        lines.extend(["", "## Archive warnings", ""])
        for warning in manifest.warnings:
            lines.append(f"- {warning}")
    lines.append("")
    return "\n".join(lines)


def build_manifest(
    extraction: ExtractionResult, settings: Settings
) -> tuple[
    DatasetManifest,
    SchemaProfileDocument,
]:
    """Construct deterministic manifest and schema profile from extraction."""
    artifacts: list[DatasetArtifact] = []
    tables: list[TableProfile] = []
    archive_warnings = list(extraction.warnings)

    for member in extraction.members:
        artifact_id = artifact_id_for(member.normalized_path, member.sha256)
        warnings = list(member.warnings)
        table_profile: TableProfile | None = None
        try:
            table_profile = _profile_member(
                artifact_id=artifact_id,
                path=member.absolute_path,
                format_=member.format,
                settings=settings,
            )
        except Exception as exc:
            warnings.append(f"profiling failed: {exc}")
            table_profile = None

        if table_profile is not None:
            warnings.extend(table_profile.warnings)
            if table_profile.columns or table_profile.sheets or table_profile.warnings:
                tables.append(table_profile)

        role, confidence, reasons = classify_role(
            relative_path=member.normalized_path,
            format_=member.format,
            table_profile=table_profile,
        )
        artifacts.append(
            DatasetArtifact(
                id=artifact_id,
                relative_path=member.relative_path,
                normalized_path=member.normalized_path,
                format=member.format,
                mime_type=member.mime_type,
                role=role,
                role_confidence=confidence,
                role_reasons=reasons,
                size_bytes=member.size_bytes,
                compressed_size_bytes=member.compressed_size_bytes,
                sha256=member.sha256,
                table_profile=table_profile,
                warnings=list(dict.fromkeys(warnings)),
            )
        )

    artifacts.sort(key=lambda item: item.normalized_path)
    tables.sort(key=lambda item: item.artifact_id)
    manifest = DatasetManifest(
        archive_name=extraction.archive_name,
        archive_sha256=extraction.archive_sha256,
        artifacts=artifacts,
        total_files=len(artifacts),
        total_uncompressed_bytes=sum(item.size_bytes for item in artifacts),
        warnings=archive_warnings,
    )
    schema_profile = SchemaProfileDocument(
        archive_sha256=extraction.archive_sha256,
        tables=tables,
        warnings=[],
    )
    return manifest, schema_profile


def inspect_archive(
    input_path: Path,
    output_dir: Path,
    *,
    overwrite: bool = False,
    settings: Settings | None = None,
) -> InspectionResult:
    """Inspect a ZIP archive and write deterministic Stage 2 outputs."""
    resolved_settings = settings or get_settings()
    input_path = input_path.resolve()
    output_dir = output_dir.resolve()

    if output_dir.exists():
        remaining = [path for path in output_dir.rglob("*") if path.is_file()]
        if remaining and not overwrite:
            raise ArchiveInspectionError(
                f"output directory is not empty: {output_dir} (pass --overwrite)"
            )
    output_dir.mkdir(parents=True, exist_ok=True)

    connector = ArchiveZipConnector(input_path, output_dir, settings=resolved_settings)
    extraction = connector.extract()
    manifest, schema_profile = build_manifest(extraction, resolved_settings)

    manifest_path = output_dir / "manifest.json"
    schema_path = output_dir / "schema_profile.json"
    summary_path = output_dir / "inspection_summary.md"

    _atomic_write_text(manifest_path, _dump_json(manifest))
    _atomic_write_text(schema_path, _dump_json(schema_profile))
    _atomic_write_text(summary_path, _build_summary_markdown(manifest, schema_profile))

    return InspectionResult(
        manifest=manifest,
        schema_profile=schema_profile,
        summary_path=str(summary_path),
    )
