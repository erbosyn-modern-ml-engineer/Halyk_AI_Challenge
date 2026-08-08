"""Fixed, source-faithful competition pipeline for Stages 3 -> 6.

The orchestration is intentionally smaller than DVC/Kedro.  Halyk has one fixed
competition path, so the useful donor ideas are explicit stage inputs/outputs,
fail-fast ordering, and deterministic lineage rather than a new framework runtime.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from halyk_agent.adapters.archive.hashing import artifact_id_for, sha256_bytes
from halyk_agent.adapters.ocr.planner import plan_selective_ocr
from halyk_agent.app.authority import authority_from_paths
from halyk_agent.app.covenant import covenant_from_paths
from halyk_agent.app.evaluation import evaluate_from_paths
from halyk_agent.app.facts import facts_from_paths
from halyk_agent.app.ocr import load_parsed_documents, run_selective_ocr
from halyk_agent.app.routing import route_from_paths
from halyk_agent.app.transactions import transactions_from_paths
from halyk_agent.config import Settings, get_settings
from halyk_agent.dataset_access import FileOpener, resolve_dataset_path
from halyk_agent.domain.covenant_evaluation import EvaluationContext, EvaluationReport
from halyk_agent.domain.covenants.models import CovenantReport
from halyk_agent.domain.datasets import (
    ArtifactFormat,
    ArtifactRole,
    DatasetArtifact,
    DatasetManifest,
)
from halyk_agent.domain.ids import sha256_text
from halyk_agent.domain.transaction_taxonomy.models import TaxonomyReport
from halyk_agent.preflight.models import AllowedInputRef, SanitizedDatasetManifest
from halyk_agent.solver.audit import RunFileAudit
from halyk_agent.solver.errors import DatasetAdapterError
from halyk_agent.solver.fast_parse import parse_competition_documents_parallel


class CompetitionPipelineError(Exception):
    def __init__(self, message: str, *, stage: str, code: str = "PIPELINE_ERROR") -> None:
        super().__init__(message)
        self.message = message
        self.stage = stage
        self.code = code


@dataclass(frozen=True)
class MaterializedInputs:
    runtime_manifest: SanitizedDatasetManifest
    runtime_manifest_path: Path
    inspection_dir: Path
    documents_dir: Path
    template_path: Path
    ledger_path: Path
    template_payload: dict[str, Any]


@dataclass(frozen=True)
class CompetitionPipelineRun:
    materialized: MaterializedInputs
    parsed_dir: Path
    routing_dir: Path
    authority_dir: Path
    covenants_dir: Path
    facts_dir: Path
    transactions_dir: Path
    evaluation_dir: Path
    covenants: CovenantReport
    taxonomy: TaxonomyReport
    evaluation: EvaluationReport
    context: EvaluationContext
    stage_summary: dict[str, Any]


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _read_verified(
    ref: AllowedInputRef,
    *,
    opener: FileOpener,
    audit: RunFileAudit,
    purpose: str,
) -> bytes:
    source = resolve_dataset_path(ref.path)
    data = opener.read_bytes(source)
    if len(data) != ref.size:
        raise DatasetAdapterError(f"allowlisted size mismatch: {source}")
    digest = sha256_bytes(data)
    if digest != ref.sha256:
        raise DatasetAdapterError(f"allowlisted SHA-256 mismatch: {source}")
    audit.record(source, component="dataset", purpose=purpose, data=data)
    return data


def _runtime_ref(path: Path, source: AllowedInputRef) -> AllowedInputRef:
    return AllowedInputRef(
        path=path.resolve().as_posix(),
        sha256=source.sha256,
        size=source.size,
        role=source.role,
    )


def materialize_allowlisted_inputs(
    manifest: SanitizedDatasetManifest,
    *,
    workspace: Path,
    opener: FileOpener,
    audit: RunFileAudit,
) -> MaterializedInputs:
    """Copy only sanitized allowlisted inputs into an isolated runtime workspace."""

    input_dir = workspace / "input"
    inspection_dir = workspace / "inspection"
    extracted_docs = inspection_dir / "extracted" / "documents"
    input_dir.mkdir(parents=True, exist_ok=True)
    extracted_docs.mkdir(parents=True, exist_ok=True)

    template_data = _read_verified(
        manifest.submission_template,
        opener=opener,
        audit=audit,
        purpose="submission_template",
    )
    template_path = input_dir / "submission_template.json"
    template_path.write_bytes(template_data)
    try:
        template_payload = json.loads(template_data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DatasetAdapterError(f"invalid submission template JSON: {exc}") from exc
    if not isinstance(template_payload, dict):
        raise DatasetAdapterError("submission template must be a JSON object")

    ledger_data = _read_verified(
        manifest.primary_ledger,
        opener=opener,
        audit=audit,
        purpose="primary_ledger",
    )
    ledger_path = input_dir / "master_ledger_2025.csv"
    ledger_path.write_bytes(ledger_data)

    runtime_cases: list[AllowedInputRef] = []
    for index, ref in enumerate(manifest.case_descriptions):
        data = _read_verified(ref, opener=opener, audit=audit, purpose="case_description")
        name = Path(ref.path).name or f"case-{index}.md"
        destination = input_dir / "cases" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        runtime_cases.append(_runtime_ref(destination, ref))

    runtime_docs: list[AllowedInputRef] = []
    artifacts: list[DatasetArtifact] = []
    seen_names: dict[str, str] = {}
    total_bytes = 0
    for ref in manifest.document_files:
        data = _read_verified(ref, opener=opener, audit=audit, purpose="document")
        name = Path(ref.path).name
        previous = seen_names.get(name)
        if previous is not None and previous != ref.sha256:
            raise DatasetAdapterError(f"document basename collision: {name}")
        seen_names[name] = ref.sha256
        destination = extracted_docs / name
        destination.write_bytes(data)
        runtime_docs.append(_runtime_ref(destination, ref))
        relative = f"documents/{name}"
        artifacts.append(
            DatasetArtifact(
                id=artifact_id_for(relative, ref.sha256),
                relative_path=relative,
                normalized_path=relative,
                format=ArtifactFormat.PDF,
                mime_type="application/pdf",
                role=ArtifactRole.DOCUMENT,
                role_confidence=1.0,
                role_reasons=["sanitized_manifest.document_files"],
                size_bytes=ref.size,
                compressed_size_bytes=ref.size,
                sha256=ref.sha256,
            )
        )
        total_bytes += ref.size

    if not artifacts:
        raise DatasetAdapterError("sanitized manifest contains no document files")

    runtime_manifest = SanitizedDatasetManifest(
        case_descriptions=runtime_cases,
        primary_ledger=_runtime_ref(ledger_path, manifest.primary_ledger),
        submission_template=_runtime_ref(template_path, manifest.submission_template),
        documents_dir=extracted_docs.resolve().as_posix(),
        document_files=runtime_docs,
        technical_noise=[],
        ignored=manifest.ignored,
        quarantined=manifest.quarantined,
    )
    runtime_manifest_path = input_dir / "sanitized_manifest.json"
    _write_text(runtime_manifest_path, runtime_manifest.model_dump_json(indent=2) + "\n")

    identity_payload = "|".join(
        f"{artifact.normalized_path}:{artifact.sha256}:{artifact.size_bytes}"
        for artifact in sorted(artifacts, key=lambda item: item.normalized_path)
    )
    inspection_manifest = DatasetManifest(
        archive_name="sanitized-runtime",
        archive_sha256=sha256_text(identity_payload),
        artifacts=sorted(artifacts, key=lambda item: item.id),
        total_files=len(artifacts),
        total_uncompressed_bytes=total_bytes,
        warnings=[],
    )
    _write_text(
        inspection_dir / "manifest.json", inspection_manifest.model_dump_json(indent=2) + "\n"
    )

    return MaterializedInputs(
        runtime_manifest=runtime_manifest,
        runtime_manifest_path=runtime_manifest_path,
        inspection_dir=inspection_dir,
        documents_dir=extracted_docs,
        template_path=template_path,
        ledger_path=ledger_path,
        template_payload=template_payload,
    )


def run_competition_pipeline(
    manifest: SanitizedDatasetManifest,
    *,
    workspace: Path,
    opener: FileOpener,
    audit: RunFileAudit,
    settings: Settings | None = None,
) -> CompetitionPipelineRun:
    """Run the deterministic public competition path from allowlisted sources to Stage 6."""

    resolved_settings = settings or get_settings()
    materialized = materialize_allowlisted_inputs(
        manifest,
        workspace=workspace,
        opener=opener,
        audit=audit,
    )

    parsed_fast = workspace / "parsed_fast"
    try:
        parse_report = parse_competition_documents_parallel(
            materialized.inspection_dir,
            parsed_fast,
            settings=resolved_settings,
        )
    except Exception as exc:
        raise CompetitionPipelineError(str(exc), stage="parse") from exc

    _, parsed_documents = load_parsed_documents(parsed_fast)
    source_paths = {
        document.artifact_id: str(
            (materialized.documents_dir / Path(document.source_file).name).resolve()
        )
        for document in parsed_documents
    }
    ocr_plan = plan_selective_ocr(
        parsed_documents,
        source_paths=source_paths,
        only_required=True,
        max_pages=64,
        total_pdfs=sum(1 for doc in parsed_documents if doc.source_file.lower().endswith(".pdf")),
        total_pages=sum(len(doc.pages) for doc in parsed_documents),
    )
    parsed_dir = parsed_fast
    ocr_summary: dict[str, Any] = {
        "blocking_pages": ocr_plan.blocking_pages,
        "selected_pages": len(ocr_plan.selections),
        "executed": False,
    }
    if ocr_plan.selections:
        parsed_ocr = workspace / "parsed_ocr"
        try:
            ocr_report = asyncio.run(
                run_selective_ocr(
                    parsed_fast,
                    parsed_ocr,
                    overwrite=False,
                    backend_name=None,
                    languages=["eng", "rus", "kaz"],
                    only_required=True,
                    max_pages=64,
                    timeout=60.0,
                    scale=2.0,
                    psm=6,
                    source_roots=[materialized.documents_dir],
                )
            )
        except Exception as exc:
            raise CompetitionPipelineError(str(exc), stage="ocr") from exc
        if ocr_report.remaining_blocking_pages:
            raise CompetitionPipelineError(
                f"OCR left {ocr_report.remaining_blocking_pages} blocking pages",
                stage="ocr",
                code="OCR_BLOCKING_REMAINS",
            )
        parsed_dir = parsed_ocr
        ocr_summary = {
            "blocking_pages": ocr_plan.blocking_pages,
            "selected_pages": len(ocr_plan.selections),
            "executed": True,
            "attempted_pages": ocr_report.attempted_pages,
            "succeeded_pages": ocr_report.succeeded_pages,
            "remaining_blocking_pages": ocr_report.remaining_blocking_pages,
        }

    routing_dir = workspace / "routing"
    authority_dir = workspace / "authority"
    covenants_dir = workspace / "covenants"
    facts_dir = workspace / "facts"
    transactions_dir = workspace / "transactions"
    evaluation_dir = workspace / "evaluation"

    try:
        routing = route_from_paths(
            dataset_manifest=materialized.runtime_manifest_path,
            parsed_dir=parsed_dir,
            output_dir=routing_dir,
            overwrite=False,
            strict=False,
        )
    except Exception as exc:
        raise CompetitionPipelineError(str(exc), stage="routing") from exc

    try:
        authority = authority_from_paths(
            routing_dir=routing_dir,
            parsed_dir=parsed_dir,
            output_dir=authority_dir,
            overwrite=False,
            strict=False,
        )
    except Exception as exc:
        raise CompetitionPipelineError(str(exc), stage="authority") from exc

    try:
        covenants = covenant_from_paths(
            authority_dir=authority_dir,
            parsed_dir=parsed_dir,
            template_path=materialized.template_path,
            output_dir=covenants_dir,
            overwrite=False,
        )
    except Exception as exc:
        raise CompetitionPipelineError(str(exc), stage="covenants") from exc

    try:
        facts = facts_from_paths(
            authority_dir=authority_dir,
            covenants_dir=covenants_dir,
            parsed_dir=parsed_dir,
            output_dir=facts_dir,
            ledger_path=materialized.ledger_path,
            overwrite=False,
            allow_network_models=False,
            settings=resolved_settings,
        )
    except Exception as exc:
        raise CompetitionPipelineError(str(exc), stage="facts") from exc

    try:
        taxonomy = transactions_from_paths(
            routing_dir=routing_dir,
            covenants_dir=covenants_dir,
            facts_dir=facts_dir,
            ledger_path=materialized.ledger_path,
            output_dir=transactions_dir,
            overwrite=False,
        )
    except Exception as exc:
        raise CompetitionPipelineError(str(exc), stage="transactions") from exc

    try:
        evaluation = evaluate_from_paths(
            covenants_dir=covenants_dir,
            transactions_dir=transactions_dir,
            output_dir=evaluation_dir,
            overwrite=False,
        )
    except Exception as exc:
        raise CompetitionPipelineError(str(exc), stage="evaluation") from exc

    evaluation_scenarios = {plan.scenario_id for plan in evaluation.plans}
    context = EvaluationContext(
        amount_contract_version=taxonomy.manifest.amount_contract_version,
        calculation_inputs=tuple(
            item for item in taxonomy.calculation_inputs if item.scenario_id in evaluation_scenarios
        ),
        selector_coverage=taxonomy.selector_coverage,
        definition_readiness=taxonomy.definition_readiness,
    )
    stage_summary = {
        "parse": {
            "successful": parse_report.successful,
            "partial": parse_report.partial,
            "failed": parse_report.failed,
            "unsupported": parse_report.unsupported,
        },
        "ocr": ocr_summary,
        "routing": {
            "resolved_documents": routing.manifest.resolved_document_count,
            "unresolved_documents": routing.manifest.unresolved_document_count,
            "conflicts": routing.manifest.conflict_count,
        },
        "authority": {
            "classified_documents": authority.manifest.classified_count,
            "unknown_documents": authority.manifest.unknown_count,
            "conflicts": authority.manifest.conflict_count,
        },
        "covenants": {
            "definitions": covenants.manifest.definition_count,
            "supported": covenants.manifest.supported_count,
            "failures": covenants.manifest.failure_count,
        },
        "facts": {
            "accepted": facts.manifest.accepted_count,
            "unresolved": facts.manifest.unresolved_count,
            "model_calls": facts.manifest.model_call_count,
        },
        "transactions": {
            "calculation_inputs": taxonomy.manifest.calculation_input_count,
            "definition_ready": taxonomy.manifest.definition_ready_count,
            "definition_unresolved": taxonomy.manifest.definition_unresolved_count,
            "adjustments": taxonomy.manifest.adjustment_event_count,
        },
        "evaluation": {
            "results": evaluation.manifest.result_count,
            "resolved": evaluation.manifest.resolved_count,
            "unresolved": evaluation.manifest.unresolved_count,
            "errors": evaluation.manifest.error_count,
            "not_activated": evaluation.manifest.not_activated_count,
        },
    }
    return CompetitionPipelineRun(
        materialized=materialized,
        parsed_dir=parsed_dir,
        routing_dir=routing_dir,
        authority_dir=authority_dir,
        covenants_dir=covenants_dir,
        facts_dir=facts_dir,
        transactions_dir=transactions_dir,
        evaluation_dir=evaluation_dir,
        covenants=covenants,
        taxonomy=taxonomy,
        evaluation=evaluation,
        context=context,
        stage_summary=stage_summary,
    )
