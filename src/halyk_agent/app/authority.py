"""Document taxonomy and authority application service (Stage 5C)."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from halyk_agent.adapters.authority.io import (
    AuthorityIOError,
    has_structural_failure,
    load_document_links,
    load_identity_evidence_hash,
    load_routing_manifest,
    write_authority_outputs,
)
from halyk_agent.app.ocr import load_parsed_documents
from halyk_agent.domain.authority.engine import run_authority
from halyk_agent.domain.authority.models import AuthorityReport, AuthorityStatus
from halyk_agent.domain.ids import sha256_text


class AuthorityServiceError(Exception):
    def __init__(self, message: str, *, code: str = "AUTHORITY_ERROR") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def _publish_staged(stage_dir: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for path in sorted(stage_dir.iterdir()):
        dest = output_dir / path.name
        if dest.exists():
            if dest.is_dir():
                shutil.rmtree(dest)
            else:
                dest.unlink()
        os.replace(path, dest)


def _replace_published_outputs(stage_dir: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    backup_dir: Path | None = None
    existing = list(output_dir.iterdir())
    if existing:
        backup_dir = Path(tempfile.mkdtemp(prefix=".authority-prev-", dir=str(output_dir.parent)))
        for child in existing:
            os.replace(child, backup_dir / child.name)
    try:
        _publish_staged(stage_dir, output_dir)
    except Exception:
        if backup_dir is not None:
            for child in list(output_dir.iterdir()):
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            for child in backup_dir.iterdir():
                os.replace(child, output_dir / child.name)
        raise
    finally:
        if backup_dir is not None and backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)


def _parsed_input_identity(parsed_dir: Path, document_count: int) -> dict[str, Any]:
    report_path = parsed_dir / "parse_report.json"
    evidence_path = parsed_dir / "evidence_catalog.jsonl"
    ocr_report = parsed_dir / "ocr_report.json"
    identity: dict[str, Any] = {
        "parse_report_sha256": (
            sha256_text(report_path.read_text(encoding="utf-8")) if report_path.is_file() else ""
        ),
        "evidence_catalogue_sha256": (
            sha256_text(evidence_path.read_text(encoding="utf-8"))
            if evidence_path.is_file()
            else ""
        ),
        "document_count": document_count,
        "ocr_enriched": ocr_report.is_file(),
    }
    if ocr_report.is_file():
        identity["ocr_report_sha256"] = sha256_text(ocr_report.read_text(encoding="utf-8"))
    return identity


def authority_from_paths(
    *,
    routing_dir: Path,
    parsed_dir: Path,
    output_dir: Path,
    overwrite: bool = False,
    strict: bool = False,
) -> AuthorityReport:
    """
    Application boundary: Stage 5B routing outputs + OCR-enriched parses → authority.

    Does not rediscover raw datasets. Does not open ground-truth files.
    """
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()) and not overwrite:
        raise AuthorityServiceError(
            f"output directory not empty (use --overwrite): {output_dir}",
            code="OUTPUT_EXISTS",
        )

    routing_dir = routing_dir.resolve()
    parsed_dir = parsed_dir.resolve()
    try:
        routing_manifest = load_routing_manifest(routing_dir / "routing_manifest.json")
        document_links = load_document_links(routing_dir / "document_links.jsonl")
        identity_hash = load_identity_evidence_hash(routing_dir / "identity_evidence.jsonl")
        _, documents = load_parsed_documents(parsed_dir)
        parsed_identity = _parsed_input_identity(parsed_dir, len(documents))
    except AuthorityIOError as exc:
        raise AuthorityServiceError(exc.message, code=exc.code) from exc
    except Exception as exc:
        raise AuthorityServiceError(str(exc), code="INPUT_LOAD") from exc

    report = run_authority(
        documents=tuple(documents),
        document_links=document_links,
        routing_manifest=routing_manifest,
        identity_evidence_hash=identity_hash,
        parsed_input_identity=parsed_identity,
    )

    stage_dir = Path(tempfile.mkdtemp(prefix=".authority-", dir=str(output_dir.parent)))
    try:
        write_authority_outputs(report, stage_dir)
        _replace_published_outputs(stage_dir, output_dir)
    except Exception:
        shutil.rmtree(stage_dir, ignore_errors=True)
        raise
    finally:
        if stage_dir.exists():
            shutil.rmtree(stage_dir, ignore_errors=True)

    if strict and has_structural_failure(report):
        raise AuthorityServiceError(
            "strict mode: unresolved authority conflicts present",
            code="STRICT_FAILURE",
        )
    return report


def summary_dict(report: AuthorityReport) -> dict[str, Any]:
    m = report.manifest
    return {
        "documents": m.document_count,
        "classified": m.classified_count,
        "unknown": m.unknown_count,
        "decisions": m.decision_count,
        "conflicts": m.conflict_count,
        "missing_authority": m.missing_authority_count,
        "authoritative": sum(
            1 for d in report.decisions if d.status is AuthorityStatus.AUTHORITATIVE
        ),
    }


def print_authority_summary(report: AuthorityReport) -> None:
    data = summary_dict(report)
    print("authority complete")
    print(f"documents_classified={data['classified']}")
    print(f"unknown={data['unknown']}")
    print(f"authority_decisions={data['decisions']}")
    print(f"authoritative={data['authoritative']}")
    print(f"conflicts={data['conflicts']}")
    print(f"missing_authority_domains={data['missing_authority']}")


def report_to_json(report: AuthorityReport) -> str:
    return json.dumps(summary_dict(report), ensure_ascii=False, indent=2, sort_keys=True)
