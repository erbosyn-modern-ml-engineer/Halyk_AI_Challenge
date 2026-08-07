"""Scenario and entity routing application service (Stage 5B)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from halyk_agent.adapters.routing.io import (
    RoutingIOError,
    has_structural_failure,
    load_evidence_catalogue,
    load_ledger_csv,
    load_template_answers,
    write_routing_outputs,
)
from halyk_agent.app.ocr import load_parsed_documents
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


def _resolve_allowlisted(path_str: str) -> Path:
    path = Path(path_str)
    if not path.is_file():
        raise RoutingServiceError(f"allowlisted input missing: {path}", code="MISSING_INPUT")
    return path


def route_entities(
    *,
    manifest: SanitizedDatasetManifest,
    documents: tuple[CanonicalDocument, ...],
    ledger_rows: tuple[LedgerRow, ...],
    template_answers: dict[str, Any],
    evidence_catalogue: tuple[EvidenceSpan, ...] = (),
    txn_id_parser: TxnIdParserConfig | None = None,
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
            dataset_manifest_payload=manifest.model_dump(mode="json"),
            txn_id_parser=txn_id_parser,
        )
    except ScenarioDiscoveryError as exc:
        raise RoutingServiceError(str(exc), code="SCENARIO_DISCOVERY") from exc
    except RoutingEngineError as exc:
        raise RoutingServiceError(exc.message, code=exc.code) from exc


def route_from_paths(
    *,
    dataset_manifest: Path,
    parsed_dir: Path,
    output_dir: Path,
    overwrite: bool = False,
    strict: bool = False,
) -> RoutingReport:
    """Application boundary: load allowlisted inputs, route, write outputs."""
    if output_dir.exists() and any(output_dir.iterdir()) and not overwrite:
        raise RoutingServiceError(
            f"output directory not empty (use --overwrite): {output_dir}",
            code="OUTPUT_EXISTS",
        )
    try:
        manifest = load_sanitized_manifest(dataset_manifest)
    except Exception as exc:
        raise RoutingServiceError(
            f"failed to load sanitized manifest: {exc}",
            code="MANIFEST_LOAD",
        ) from exc

    template_path = _resolve_allowlisted(manifest.submission_template.path)
    ledger_path = _resolve_allowlisted(manifest.primary_ledger.path)

    try:
        template_answers = load_template_answers(template_path)
        ledger_rows = load_ledger_csv(ledger_path)
        _, documents = load_parsed_documents(parsed_dir)
        evidence = load_evidence_catalogue(parsed_dir / "evidence_catalog.jsonl")
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
    )
    write_routing_outputs(report, output_dir)

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
