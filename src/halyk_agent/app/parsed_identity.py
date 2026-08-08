"""Semantic identity for parsed/OCR inputs without runtime telemetry entropy."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from halyk_agent.domain.ids import sha256_text

_STABLE_OCR_BACKEND_FIELDS = (
    "kind",
    "backend_version",
    "language_data_identity",
    "languages",
    "render_scale",
    "page_segmentation_mode",
    "configuration_hash",
)


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def semantic_parsed_input_identity(
    parsed_dir: Path,
    *,
    document_count: int,
) -> dict[str, Any]:
    """Return stable parser lineage derived only from semantic/configuration facts.

    Raw parse/OCR reports intentionally contain operational fields such as
    per-attempt durations, cache byte counts, executable paths and materialized
    source paths. Those are useful diagnostics but are not dataset/parser identity
    and must not perturb downstream authority/evaluation hashes across fresh runs.
    """

    parse_payload = _read_json_object(parsed_dir / "parse_report.json")
    ocr_path = parsed_dir / "ocr_report.json"
    ocr_payload = _read_json_object(ocr_path)
    evidence_path = parsed_dir / "evidence_catalog.jsonl"

    identity: dict[str, Any] = {
        "document_count": document_count,
        "ocr_enriched": ocr_path.is_file(),
        "evidence_catalogue_sha256": (
            sha256_text(evidence_path.read_text(encoding="utf-8"))
            if evidence_path.is_file()
            else ""
        ),
    }

    # Stable batch semantics; exclude cache_hits and every per-attempt duration.
    for key in (
        "schema_version",
        "profile",
        "total_candidates",
        "successful",
        "partial",
        "failed",
        "unsupported",
    ):
        if key in parse_payload:
            identity[f"parse_{key}"] = parse_payload[key]

    backend = ocr_payload.get("backend")
    if isinstance(backend, dict):
        identity["ocr_backend"] = {
            key: backend[key] for key in _STABLE_OCR_BACKEND_FIELDS if key in backend
        }

    if ocr_payload:
        for key in (
            "schema_version",
            "selected_pages",
            "attempted_pages",
            "succeeded_pages",
            "failed_pages",
            "remaining_blocking_pages",
            "documents_processed",
            "offline_ready",
            "blocked_reason",
        ):
            if key in ocr_payload:
                identity[f"ocr_{key}"] = ocr_payload[key]

    return identity
