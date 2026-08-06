"""Discover competition dataset roles without hard-coded absolute paths."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from halyk_agent.solver.audit import RunFileAudit
from halyk_agent.solver.dataset.answer_key_guard import is_answer_key_payload
from halyk_agent.solver.dataset.ignore import ignore_artifact
from halyk_agent.solver.dataset.models import DatasetFileRef, DatasetManifest
from halyk_agent.solver.errors import DatasetAdapterError
from halyk_agent.solver.failures import FailureEvent, FailureMode


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ref(path: Path, data: bytes, role: str) -> DatasetFileRef:
    return DatasetFileRef(
        path=str(path.as_posix()),
        sha256=_sha256_bytes(data),
        size=len(data),
        role=role,
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
    required = {"txn_id", "amount", "currency"}
    return required.issubset(set(header))


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


def discover_dataset(
    root: Path,
    *,
    audit: RunFileAudit | None = None,
    failure_events: list[FailureEvent] | None = None,
) -> DatasetManifest:
    """Discover dataset roles under ``root`` (deterministic, sorted walk)."""
    root = root.resolve()
    if not root.is_dir():
        raise DatasetAdapterError(f"dataset root is not a directory: {root}")

    ignored = []
    case_descriptions: list[DatasetFileRef] = []
    technical_noise: list[DatasetFileRef] = []
    document_files: list[DatasetFileRef] = []
    ledgers: list[DatasetFileRef] = []
    templates: list[DatasetFileRef] = []
    gt_candidate: DatasetFileRef | None = None
    documents_dir: Path | None = None

    all_files = sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: p.as_posix())
    for path in all_files:
        ignored_item = ignore_artifact(path)
        if ignored_item is not None:
            ignored.append(ignored_item)
            if failure_events is not None:
                failure_events.append(
                    FailureEvent(
                        event_id=f"ignore-{len(failure_events) + 1}",
                        run_id=audit.run_id if audit else "discover",
                        stage="dataset_adapter",
                        interaction_edge="filesystem->adapter",
                        fault_side="input",
                        failure_mode=FailureMode.TECHNICAL_ARTIFACT_IGNORED,
                        observed_symptom=ignored_item.ignore_rule,
                        evidence_refs=[ignored_item.path],
                        recommended_repair_owner="dataset_adapter",
                        recovered=True,
                    )
                )
            continue

        data = path.read_bytes()
        rel_parent = path.parent

        # Answer-key / ground truth: discover candidate without solver audit.record.
        if path.suffix.lower() == ".json":
            name_l = path.name.lower()
            if "ground_truth" in name_l:
                gt_candidate = _ref(path, data, "ground_truth_candidate")
                continue
            try:
                obj = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                obj = None
            if obj is not None and is_answer_key_payload(obj):
                gt_candidate = _ref(path, data, "ground_truth_candidate")
                continue
            if obj is not None and _looks_like_submission_template(obj):
                templates.append(_ref(path, data, "submission_template"))
                continue

        if _looks_like_ledger(path, data):
            # Prefer root-level ledger over documents/*.csv noise
            ledgers.append(_ref(path, data, "primary_ledger"))
            continue

        if _looks_like_case_markdown(path, data):
            case_descriptions.append(_ref(path, data, "case_description"))
            continue

        if path.suffix.lower() == ".pdf":
            document_files.append(_ref(path, data, "document"))
            if documents_dir is None or len(list(rel_parent.glob("*.pdf"))) > len(
                list((documents_dir or rel_parent).glob("*.pdf"))
            ):
                documents_dir = rel_parent
            continue

        # Remaining odd files under documents/ are technical noise (not evidence).
        if "documents" in {p.lower() for p in path.parts}:
            technical_noise.append(_ref(path, data, "technical_noise"))

    # Choose primary ledger: prefer files directly under root
    primary_ledger = None
    if ledgers:
        root_ledgers = [item for item in ledgers if Path(item.path).parent == root]
        primary_ledger = sorted(root_ledgers or ledgers, key=lambda r: r.path)[0]

    submission_template = None
    if templates:
        submission_template = sorted(templates, key=lambda r: r.path)[0]

    if submission_template is None:
        raise DatasetAdapterError("submission template not found")
    if primary_ledger is None:
        raise DatasetAdapterError("primary ledger CSV not found")
    if not case_descriptions:
        raise DatasetAdapterError("case description files not found")

    return DatasetManifest(
        root=str(root.as_posix()),
        case_descriptions=sorted(case_descriptions, key=lambda r: r.path),
        primary_ledger=primary_ledger,
        submission_template=submission_template,
        documents_dir=str(documents_dir.as_posix()) if documents_dir else None,
        document_files=sorted(document_files, key=lambda r: r.path),
        ground_truth_candidate=gt_candidate,
        technical_noise=sorted(technical_noise, key=lambda r: r.path),
        ignored=sorted(ignored, key=lambda r: r.path),
    )
