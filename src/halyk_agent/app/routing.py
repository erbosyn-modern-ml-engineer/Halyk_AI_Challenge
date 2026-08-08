"""Scenario and entity routing application service (Stage 5B.2)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from halyk_agent.adapters.routing.io import (
    RoutingIOError,
    has_structural_failure,
    load_evidence_catalogue,
    load_ledger_csv_bytes,
    load_template_answers_bytes,
    write_routing_outputs,
)
from halyk_agent.app.ocr import load_parsed_documents
from halyk_agent.app.parsed_identity import semantic_parsed_input_identity
from halyk_agent.dataset_access import (
    FileOpener,
    LeakageAttemptError,
    RecordingFileOpener,
    assert_opens_allowlisted,
    require_audited_opener,
    resolve_dataset_path,
    validate_manifest_paths,
)
from halyk_agent.domain.evidence import EvidenceSpan
from halyk_agent.domain.parsing import CanonicalDocument
from halyk_agent.domain.routing.engine import RoutingEngineError, run_routing
from halyk_agent.domain.routing.models import LedgerRow, RoutingReport, TxnIdParserConfig
from halyk_agent.domain.routing.scenarios import ScenarioDiscoveryError
from halyk_agent.preflight.models import SanitizedDatasetManifest
from halyk_agent.preflight.service import load_sanitized_manifest


class RoutingServiceError(Exception):
    def __init__(self, message: str, *, code: str = "ROUTING_ERROR") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def _manifest_identity_payload(manifest: SanitizedDatasetManifest) -> dict[str, Any]:
    """Path-independent identity payload for a sanitized dataset manifest.

    Runtime materialization intentionally rewrites allowlisted files into a private
    workspace. Absolute workspace paths are execution details, not dataset identity.
    Keep basenames plus source hashes/sizes/roles so two fresh runs bind to the same
    source universe without leaking temporary-directory entropy downstream.
    """

    payload = manifest.model_dump(mode="json")

    def normalize_ref(item: object) -> None:
        if not isinstance(item, dict):
            return
        raw = item.get("path")
        if isinstance(raw, str):
            item["path"] = raw.replace("\\", "/").rsplit("/", 1)[-1]

    for key in ("case_descriptions", "document_files", "technical_noise", "ignored", "quarantined"):
        values = payload.get(key)
        if isinstance(values, list):
            for item in values:
                normalize_ref(item)
    normalize_ref(payload.get("primary_ledger"))
    normalize_ref(payload.get("submission_template"))
    if payload.get("documents_dir") is not None:
        payload["documents_dir"] = "<documents>"
    return payload


def route_entities(
    *,
    manifest: SanitizedDatasetManifest,
    documents: tuple[CanonicalDocument, ...],
    ledger_rows: tuple[LedgerRow, ...],
    template_answers: dict[str, Any],
    evidence_catalogue: tuple[EvidenceSpan, ...] = (),
    txn_id_parser: TxnIdParserConfig | None = None,
    parsed_input_identity: dict[str, Any] | None = None,
    ledger_source_sha256: str | None = None,
) -> RoutingReport:
    """
    Core typed routing API.

    Consumes SanitizedDatasetManifest + structured inputs. Does not accept a
    raw dataset root and does not rediscover the dataset.
    """
    try:
        return run_routing(
            template_answers=template_answers,
            ledger_rows=ledger_rows,
            documents=documents,
            evidence_catalogue=evidence_catalogue,
            dataset_manifest_payload=_manifest_identity_payload(manifest),
            txn_id_parser=txn_id_parser,
            parsed_input_identity=parsed_input_identity,
            ledger_source_sha256=ledger_source_sha256,
        )
    except ScenarioDiscoveryError as exc:
        raise RoutingServiceError(str(exc), code="SCENARIO_DISCOVERY") from exc
    except RoutingEngineError as exc:
        raise RoutingServiceError(exc.message, code=exc.code) from exc


def _parsed_input_identity(parsed_dir: Path, documents: list[CanonicalDocument]) -> dict[str, Any]:
    """Compatibility wrapper for the shared semantic parser identity."""

    return semantic_parsed_input_identity(parsed_dir, document_count=len(documents))


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
    """
    Publish only after staged outputs are complete.

    Prefer: build staged set → validate → replace published children.
    Previous published children are moved aside first so a mid-publish failure
    can restore them; they are deleted only after the new set is in place.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    backup_dir: Path | None = None
    existing = list(output_dir.iterdir())
    if existing:
        backup_dir = Path(tempfile.mkdtemp(prefix=".routing-prev-", dir=str(output_dir.parent)))
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


def route_from_paths(
    *,
    dataset_manifest: Path,
    parsed_dir: Path,
    output_dir: Path,
    overwrite: bool = False,
    strict: bool = False,
    opener: FileOpener | None = None,
) -> RoutingReport:
    """
    Application boundary: audited allowlisted reads → route → staged publish.

    Failures before publish leave zero visible routing outputs in output_dir
    (aside from an empty/pre-existing directory when overwrite is used).
    """
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()) and not overwrite:
        raise RoutingServiceError(
            f"output directory not empty (use --overwrite): {output_dir}",
            code="OUTPUT_EXISTS",
        )

    # Fail closed before any dataset role reads.
    file_opener = require_audited_opener(opener or RecordingFileOpener())

    try:
        manifest = load_sanitized_manifest(dataset_manifest)
    except Exception as exc:
        raise RoutingServiceError(
            f"failed to load sanitized manifest: {exc}",
            code="MANIFEST_LOAD",
        ) from exc

    try:
        allowed, banned = validate_manifest_paths(manifest)
    except LeakageAttemptError:
        raise

    template_path = resolve_dataset_path(manifest.submission_template.path)
    ledger_path = resolve_dataset_path(manifest.primary_ledger.path)
    if not template_path.is_file():
        raise RoutingServiceError(
            f"allowlisted input missing: {template_path}", code="MISSING_INPUT"
        )
    if not ledger_path.is_file():
        raise RoutingServiceError(f"allowlisted input missing: {ledger_path}", code="MISSING_INPUT")

    try:
        template_bytes = file_opener.read_bytes(template_path)
        ledger_bytes = file_opener.read_bytes(ledger_path)
        assert_opens_allowlisted(file_opener, allowed=allowed, banned=banned)

        template_answers = load_template_answers_bytes(template_bytes)
        ledger_rows = load_ledger_csv_bytes(
            ledger_bytes,
            source_file=ledger_path.name,
        )
        ledger_source_sha256 = hashlib.sha256(ledger_bytes).hexdigest()
        _, documents = load_parsed_documents(parsed_dir)
        evidence = load_evidence_catalogue(parsed_dir / "evidence_catalog.jsonl")
        parsed_identity = _parsed_input_identity(parsed_dir, documents)
        parsed_identity["ledger_source_sha256"] = ledger_source_sha256
    except LeakageAttemptError:
        raise
    except RoutingIOError as exc:
        raise RoutingServiceError(exc.message, code=exc.code) from exc
    except Exception as exc:
        raise RoutingServiceError(str(exc), code="INPUT_LOAD") from exc

    report = route_entities(
        manifest=manifest,
        documents=tuple(documents),
        ledger_rows=ledger_rows,
        template_answers=template_answers,
        evidence_catalogue=evidence,
        parsed_input_identity=parsed_identity,
        ledger_source_sha256=ledger_source_sha256,
    )

    # Stage outputs; publish only after a second open audit.
    stage_dir = Path(tempfile.mkdtemp(prefix=".routing-", dir=str(output_dir.parent)))
    try:
        write_routing_outputs(report, stage_dir)
        open_audit = {
            "schema_version": "halyk.routing_open_audit.v1",
            "opened_files": [
                {"path": resolve_dataset_path(p).as_posix(), "role": "dataset"}
                for p in file_opener.opened_paths
            ],
            "allowed": sorted(p.as_posix() for p in allowed),
            "quarantined_banned_count": len(banned),
        }
        (stage_dir / "routing_open_audit.json").write_text(
            json.dumps(open_audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        assert_opens_allowlisted(file_opener, allowed=allowed, banned=banned)
        # Staged set is complete; replace published artifacts atomically-ish.
        _replace_published_outputs(stage_dir, output_dir)
    except Exception:
        shutil.rmtree(stage_dir, ignore_errors=True)
        raise
    finally:
        if stage_dir.exists():
            shutil.rmtree(stage_dir, ignore_errors=True)

    if strict and has_structural_failure(report):
        raise RoutingServiceError(
            "strict mode: structural routing conflicts make one or more scenarios unusable",
            code="STRICT_FAILURE",
        )
    return report


def summary_dict(report: RoutingReport) -> dict[str, Any]:
    m = report.manifest
    return {
        "scenarios": m.scenario_count,
        "scenario_accounts": {
            route.scenario_id: list(route.account_ids) for route in report.scenario_routes
        },
        "transactions_linked": m.scenario_transaction_count,
        "transaction_links_total": m.transaction_link_count,
        "documents_linked": m.resolved_document_count,
        "documents_unresolved": m.unresolved_document_count,
        "multi_scenario_docs": m.multi_scenario_document_count,
        "conflicts": m.conflict_count,
        "template_cells": m.template_cell_count,
        "ledger_rows": m.ledger_row_count,
    }


def print_route_summary(report: RoutingReport) -> None:
    data = summary_dict(report)
    print("routing complete")
    print(f"scenarios={data['scenarios']}")
    accounts = data["scenario_accounts"]
    for scenario_id in sorted(accounts):
        acc = ",".join(accounts[scenario_id]) or "-"
        print(f"scenario_account\t{scenario_id}\t{acc}")
    print(f"transactions_linked={data['transactions_linked']}")
    print(f"documents_linked={data['documents_linked']}")
    print(f"documents_unresolved={data['documents_unresolved']}")
    print(f"multi_scenario_docs={data['multi_scenario_docs']}")
    print(f"conflicts={data['conflicts']}")


def report_to_json(report: RoutingReport) -> str:
    return json.dumps(summary_dict(report), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
