"""Raw dataset discovery and quarantine (preflight process only)."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from halyk_agent.preflight.ignore import ignore_artifact
from halyk_agent.preflight.models import (
    AllowedInputRef,
    JsonCandidateRole,
    QuarantinedRef,
    SanitizedDatasetManifest,
)
from halyk_agent.preflight.quarantine import is_answer_key_payload
from halyk_agent.solver.errors import DatasetAdapterError


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _allowed(path: Path, data: bytes, role: str) -> AllowedInputRef:
    return AllowedInputRef(
        path=str(path.as_posix()),
        sha256=_sha256_bytes(data),
        size=len(data),
        role=role,
    )


def _quarantine(path: Path, data: bytes, reason: str) -> QuarantinedRef:
    return QuarantinedRef(
        path=str(path.as_posix()),
        sha256=_sha256_bytes(data),
        size=len(data),
        role=JsonCandidateRole.QUARANTINED_ANSWER_KEY,
        quarantine_reason=reason,
    )


def _looks_like_ledger(path: Path, data: bytes) -> bool:
    if path.suffix.lower() != ".csv":
        return False
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    reader = csv.reader(text.splitlines())
    try:
        header = [h.strip().lower() for h in next(reader)]
    except StopIteration:
        return False
    return {"txn_id", "amount", "currency"}.issubset(set(header))


def _looks_like_submission_template(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False
    if not {"team", "contact_email", "model", "answers"}.issubset(obj):
        return False
    answers = obj.get("answers")
    if not isinstance(answers, dict) or not answers:
        return False
    first = next(iter(answers.values()))
    if not isinstance(first, dict) or not first:
        return False
    cell = next(iter(first.values()))
    return isinstance(cell, dict) and {"status", "actual", "evidence_txn_id"}.issubset(cell)


def _looks_like_case_markdown(path: Path, data: bytes) -> bool:
    if path.suffix.lower() not in {".md", ".markdown", ".txt"}:
        return False
    try:
        text = data.decode("utf-8").lower()
    except UnicodeDecodeError:
        return False
    signals = ("covenant", "ковенант", "scenario", "сценари", "лимит", "limit")
    return sum(1 for s in signals if s in text) >= 2


def discover_and_sanitize(root: Path) -> tuple[SanitizedDatasetManifest, list[dict[str, str]]]:
    """Inspect raw dataset; return sanitized solver manifest + preflight inspection log.

    Inspection log records paths/roles/sizes/hashes only — never answer values.
    """
    root = root.resolve()
    if not root.is_dir():
        raise DatasetAdapterError(f"dataset root is not a directory: {root}")

    inspected: list[dict[str, str]] = []
    ignored = []
    case_descriptions: list[AllowedInputRef] = []
    technical_noise: list[AllowedInputRef] = []
    document_files: list[AllowedInputRef] = []
    ledgers: list[AllowedInputRef] = []
    templates: list[AllowedInputRef] = []
    quarantined: list[QuarantinedRef] = []
    documents_dir: Path | None = None

    all_files = sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: p.as_posix())
    for path in all_files:
        ignored_item = ignore_artifact(path)
        if ignored_item is not None:
            ignored.append(ignored_item)
            inspected.append(
                {
                    "path": ignored_item.path,
                    "role": "IGNORED",
                    "sha256": ignored_item.sha256,
                    "size": str(ignored_item.size),
                    "note": ignored_item.ignore_rule,
                }
            )
            continue

        data = path.read_bytes()
        rel_parent = path.parent

        if path.suffix.lower() == ".json":
            name_l = path.name.lower()
            if "ground_truth" in name_l:
                item = _quarantine(path, data, "filename_ground_truth")
                quarantined.append(item)
                inspected.append(
                    {
                        "path": item.path,
                        "role": item.role.value,
                        "sha256": item.sha256,
                        "size": str(item.size),
                        "note": item.quarantine_reason,
                    }
                )
                continue
            try:
                obj = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                obj = None
            if obj is not None and is_answer_key_payload(obj):
                item = _quarantine(path, data, "content_shape_answer_key")
                quarantined.append(item)
                inspected.append(
                    {
                        "path": item.path,
                        "role": item.role.value,
                        "sha256": item.sha256,
                        "size": str(item.size),
                        "note": item.quarantine_reason,
                    }
                )
                continue
            if obj is not None and _looks_like_submission_template(obj):
                ref = _allowed(path, data, "submission_template")
                templates.append(ref)
                inspected.append(
                    {
                        "path": ref.path,
                        "role": JsonCandidateRole.SUBMISSION_TEMPLATE.value,
                        "sha256": ref.sha256,
                        "size": str(ref.size),
                        "note": "allowlisted",
                    }
                )
                continue
            inspected.append(
                {
                    "path": str(path.as_posix()),
                    "role": JsonCandidateRole.UNKNOWN_JSON.value,
                    "sha256": _sha256_bytes(data),
                    "size": str(len(data)),
                    "note": "not_allowlisted",
                }
            )
            continue

        if _looks_like_ledger(path, data):
            ledgers.append(_allowed(path, data, "primary_ledger"))
            continue

        if _looks_like_case_markdown(path, data):
            case_descriptions.append(_allowed(path, data, "case_description"))
            continue

        if path.suffix.lower() == ".pdf":
            document_files.append(_allowed(path, data, "document"))
            if documents_dir is None or len(list(rel_parent.glob("*.pdf"))) > len(
                list((documents_dir or rel_parent).glob("*.pdf"))
            ):
                documents_dir = rel_parent
            continue

        if "documents" in {p.lower() for p in path.parts}:
            technical_noise.append(_allowed(path, data, "technical_noise"))

    if not templates:
        raise DatasetAdapterError("submission template not found")
    if not ledgers:
        raise DatasetAdapterError("primary ledger CSV not found")
    if not case_descriptions:
        raise DatasetAdapterError("case description files not found")

    root_ledgers = [item for item in ledgers if Path(item.path).parent == root]
    primary_ledger = sorted(root_ledgers or ledgers, key=lambda r: r.path)[0]
    root_templates = [item for item in templates if Path(item.path).parent == root]
    template_candidates = root_templates or templates
    if len(template_candidates) != 1:
        paths = sorted(item.path for item in template_candidates)
        raise DatasetAdapterError(f"ambiguous submission templates: {paths}")
    submission_template = template_candidates[0]

    manifest = SanitizedDatasetManifest(
        case_descriptions=sorted(case_descriptions, key=lambda r: r.path),
        primary_ledger=primary_ledger,
        submission_template=submission_template,
        documents_dir=str(documents_dir.as_posix()) if documents_dir else None,
        document_files=sorted(document_files, key=lambda r: r.path),
        technical_noise=sorted(technical_noise, key=lambda r: r.path),
        ignored=sorted(ignored, key=lambda r: r.path),
        quarantined=sorted(quarantined, key=lambda r: r.path),
    )
    return manifest, inspected
