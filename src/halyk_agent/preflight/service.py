"""Preflight application service: raw dataset → sanitized manifest."""

from __future__ import annotations

import json
import os
from pathlib import Path

from halyk_agent.preflight.discover import discover_and_sanitize
from halyk_agent.preflight.models import SanitizedDatasetManifest


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def run_preflight(dataset_root: Path, output_dir: Path) -> SanitizedDatasetManifest:
    """Inspect raw dataset, quarantine answer keys, write preflight_manifest.json."""
    manifest, inspected = discover_and_sanitize(dataset_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    sanitized_path = output_dir / "sanitized_manifest.json"
    payload = manifest.model_dump(mode="json")
    # Hard guarantee: no answer values exist on this DTO.
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _atomic_write(sanitized_path, text)

    preflight_report = {
        "schema_version": "halyk.preflight_manifest.v1",
        "dataset_root": str(dataset_root.resolve().as_posix()),
        "inspected_files": inspected,
        "quarantined": [item.model_dump(mode="json") for item in manifest.quarantined],
        "allowlisted": {
            "submission_template": manifest.submission_template.model_dump(mode="json"),
            "primary_ledger": manifest.primary_ledger.model_dump(mode="json"),
            "case_descriptions": [c.model_dump(mode="json") for c in manifest.case_descriptions],
            "document_count": len(manifest.document_files),
        },
        "note": (
            "Preflight may open candidate JSON solely for quarantine classification. "
            "No expected answer values are recorded."
        ),
    }
    _atomic_write(
        output_dir / "preflight_manifest.json",
        json.dumps(preflight_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    summary = "\n".join(
        [
            "# Preflight summary",
            "",
            f"- quarantined_answer_keys: {len(manifest.quarantined)}",
            f"- allowlisted_documents: {len(manifest.document_files)}",
            f"- sanitized_manifest: `{sanitized_path.as_posix()}`",
            "",
            "Competition solver must consume only the sanitized manifest.",
            "",
        ]
    )
    _atomic_write(output_dir / "preflight_summary.md", summary)
    return manifest


def load_sanitized_manifest(path: Path) -> SanitizedDatasetManifest:
    return SanitizedDatasetManifest.model_validate_json(path.read_text(encoding="utf-8"))
