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
from halyk_agent.app.parsed_identity import semantic_parsed_input_identity
from halyk_agent.config import Settings, get_settings
from halyk_agent.domain.authority.classify import classify_document
from halyk_agent.domain.authority.engine import run_authority
from halyk_agent.domain.authority.metadata import extract_metadata_bundle
from halyk_agent.domain.authority.semantic_classifier import classify_unresolved_documents
from halyk_agent.domain.authority.models import AuthorityReport, AuthorityStatus
from halyk_agent.domain.models_gateway.semantic_json import SemanticJsonGateway


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
    """Compatibility wrapper for path/timing-independent parsed lineage."""

    return semantic_parsed_input_identity(parsed_dir, document_count=document_count)


def authority_from_paths(
    *,
    routing_dir: Path,
    parsed_dir: Path,
    output_dir: Path,
    overwrite: bool = False,
    strict: bool = False,
    settings: Settings | None = None,
    semantic_gateway: SemanticJsonGateway | None = None,
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

    resolved_settings = settings or get_settings()
    semantic = None
    if resolved_settings.semantic_fallback_enabled:
        links_by_doc = {link.document_id: link for link in document_links}
        deterministic = {}
        metadata_by_id = {}
        for document in documents:
            meta = extract_metadata_bundle(document).metadata
            metadata_by_id[document.document_id] = meta
            deterministic[document.document_id] = classify_document(
                document,
                metadata=meta,
                link=links_by_doc.get(document.document_id),
            ).classification
        semantic = classify_unresolved_documents(
            documents=tuple(documents),
            deterministic=deterministic,
            metadata=metadata_by_id,
            settings=resolved_settings,
            gateway=semantic_gateway,
        )

    report = run_authority(
        documents=tuple(documents),
        document_links=document_links,
        routing_manifest=routing_manifest,
        identity_evidence_hash=identity_hash,
        parsed_input_identity=parsed_identity,
        semantic_overrides=semantic.overrides if semantic is not None else None,
    )

    stage_dir = Path(tempfile.mkdtemp(prefix=".authority-", dir=str(output_dir.parent)))
    try:
        write_authority_outputs(report, stage_dir)
        if semantic is not None:
            semantic_path = stage_dir / "semantic_document_classification.jsonl"
            semantic_text = "\n".join(
                json.dumps(item, ensure_ascii=False, sort_keys=True)
                for item in semantic.diagnostics
            )
            if semantic_text:
                semantic_text += "\n"
            semantic_path.write_text(semantic_text, encoding="utf-8", newline="\n")
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
