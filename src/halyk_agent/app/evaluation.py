"""Stage 6 application service: bind Stage 5D/5F artifacts and evaluate covenants."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from pydantic import ValidationError

from halyk_agent.adapters.evaluation.io import (
    EvaluationIOError,
    hash_covenant_definitions,
    hash_evaluation_models,
    load_calculation_inputs,
    load_definition_readiness,
    load_selector_coverage,
    load_taxonomy_manifest,
    verify_calculation_inputs_hash,
    write_evaluation_outputs,
)
from halyk_agent.adapters.facts.io import FactIOError, load_covenant_definitions
from halyk_agent.adapters.transactions.io import (
    TransactionIOError,
    manifest_file_hash,
    verify_taxonomy_readiness_hashes,
)
from halyk_agent.domain.covenant_evaluation import (
    ComplianceStatus,
    EvaluationContext,
    EvaluationExecutor,
    EvaluationManifest,
    EvaluationReport,
    EvaluationStatus,
    plan_definitions,
)
from halyk_agent.domain.covenant_evaluation.structure_validation import (
    EvaluationValidationError,
)


class EvaluationServiceError(Exception):
    def __init__(self, message: str, *, code: str = "EVALUATION_ERROR") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def assert_no_gt_access(path: Path) -> None:
    parts = tuple(part.casefold() for part in path.parts)
    if any(
        "ground_truth" in part or part.endswith("answer_key.json") or "training_target" in part
        for part in parts
    ):
        raise EvaluationServiceError(
            "ground truth / answer-key access forbidden in Stage 6",
            code="GT_FORBIDDEN",
        )


def _resolve_file(directory: Path, name: str) -> Path:
    path = directory / name
    if not path.is_file():
        raise EvaluationServiceError(f"missing required file: {path}", code="MISSING_INPUT")
    return path


def _publish_staged(stage_dir: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for path in sorted(stage_dir.iterdir()):
        destination = output_dir / path.name
        if destination.exists():
            if destination.is_dir():
                shutil.rmtree(destination)
            else:
                destination.unlink()
        os.replace(path, destination)


def _replace_published_outputs(stage_dir: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    backup_dir: Path | None = None
    existing = list(output_dir.iterdir())
    if existing:
        backup_dir = Path(tempfile.mkdtemp(prefix=".evaluation-prev-", dir=str(output_dir.parent)))
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


def evaluate_from_paths(
    *,
    covenants_dir: Path,
    transactions_dir: Path,
    output_dir: Path,
    overwrite: bool = False,
) -> EvaluationReport:
    """Evaluate Stage 5D definitions using integrity-checked Stage 5F inputs."""

    for path in (covenants_dir, transactions_dir, output_dir):
        assert_no_gt_access(path)

    if output_dir.exists() and any(output_dir.iterdir()) and not overwrite:
        raise EvaluationServiceError(
            f"output exists (pass --overwrite): {output_dir}",
            code="OUTPUT_EXISTS",
        )

    covenant_manifest_path = _resolve_file(covenants_dir, "covenant_manifest.json")
    definitions_path = _resolve_file(covenants_dir, "covenant_definitions.jsonl")
    taxonomy_manifest_path = _resolve_file(transactions_dir, "stage5f_manifest.json")
    calculation_inputs_path = _resolve_file(transactions_dir, "calculation_inputs.jsonl")
    selector_coverage_path = _resolve_file(transactions_dir, "selector_coverage.json")
    readiness_path = _resolve_file(transactions_dir, "definition_readiness.json")

    try:
        definitions = load_covenant_definitions(definitions_path)
        taxonomy_manifest = load_taxonomy_manifest(taxonomy_manifest_path)
        calculation_inputs = load_calculation_inputs(calculation_inputs_path)
        selector_coverage = load_selector_coverage(selector_coverage_path)
        definition_readiness = load_definition_readiness(readiness_path)
        verify_calculation_inputs_hash(taxonomy_manifest, calculation_inputs)
        verify_taxonomy_readiness_hashes(
            taxonomy_manifest=taxonomy_manifest,
            selector_coverage=selector_coverage,
            definition_readiness=definition_readiness,
        )
    except (EvaluationIOError, FactIOError, TransactionIOError) as exc:
        raise EvaluationServiceError(exc.message, code=exc.code) from exc
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise EvaluationServiceError(
            f"invalid Stage 5D/5F artifact schema: {exc}",
            code="INPUT_ARTIFACT_SCHEMA",
        ) from exc

    if len(selector_coverage) != taxonomy_manifest.selector_count:
        raise EvaluationServiceError(
            "selector coverage count does not match Stage 5F manifest",
            code="SELECTOR_COVERAGE_COUNT_MISMATCH",
        )
    expected_readiness_count = (
        taxonomy_manifest.definition_ready_count + taxonomy_manifest.definition_unresolved_count
    )
    if len(definition_readiness) != expected_readiness_count:
        raise EvaluationServiceError(
            "definition readiness count does not match Stage 5F manifest",
            code="DEFINITION_READINESS_COUNT_MISMATCH",
        )

    covenant_manifest_hash = manifest_file_hash(covenant_manifest_path)
    if taxonomy_manifest.covenant_manifest_hash != covenant_manifest_hash:
        raise EvaluationServiceError(
            "Stage 5F was produced from a different covenant manifest",
            code="COVENANT_MANIFEST_MISMATCH",
        )

    covenant_manifest = json.loads(covenant_manifest_path.read_text(encoding="utf-8"))
    expected_definitions_hash = covenant_manifest.get("definitions_hash")
    if not isinstance(expected_definitions_hash, str):
        raise EvaluationServiceError(
            "covenant manifest missing definitions_hash",
            code="COVENANT_DEFINITIONS_HASH_MISSING",
        )
    if hash_covenant_definitions(definitions) != expected_definitions_hash:
        raise EvaluationServiceError(
            "covenant_definitions.jsonl does not match covenant manifest",
            code="COVENANT_DEFINITIONS_HASH_MISMATCH",
        )

    expected_definition_count = covenant_manifest.get("definition_count")
    if expected_definition_count != len(definitions):
        raise EvaluationServiceError(
            "covenant definition count does not match covenant manifest",
            code="COVENANT_DEFINITION_COUNT_MISMATCH",
        )

    plans = plan_definitions(definitions)
    plan_scenarios = {plan.scenario_id for plan in plans}
    execution_inputs = tuple(
        item for item in calculation_inputs if item.scenario_id in plan_scenarios
    )
    context = EvaluationContext(
        amount_contract_version=taxonomy_manifest.amount_contract_version,
        calculation_inputs=execution_inputs,
        selector_coverage=selector_coverage,
        definition_readiness=definition_readiness,
    )
    try:
        results = EvaluationExecutor().execute_many(plans, context)
    except EvaluationValidationError as exc:
        raise EvaluationServiceError(exc.message, code=exc.code) from exc

    plan_hash = hash_evaluation_models(plans)
    result_hash = hash_evaluation_models(results)
    manifest = EvaluationManifest(
        covenant_manifest_hash=covenant_manifest_hash,
        taxonomy_manifest_hash=manifest_file_hash(taxonomy_manifest_path),
        calculation_inputs_hash=taxonomy_manifest.calculation_inputs_hash,
        selector_coverage_hash=taxonomy_manifest.selector_coverage_hash,
        definition_readiness_hash=taxonomy_manifest.definition_readiness_hash,
        plan_count=len(plans),
        result_count=len(results),
        resolved_count=sum(1 for item in results if item.status is EvaluationStatus.RESOLVED),
        unresolved_count=sum(1 for item in results if item.status is EvaluationStatus.UNRESOLVED),
        error_count=sum(1 for item in results if item.status is EvaluationStatus.ERROR),
        not_activated_count=sum(
            1 for item in results if item.status is EvaluationStatus.NOT_ACTIVATED
        ),
        compliant_count=sum(
            1 for item in results if item.compliance_status is ComplianceStatus.COMPLIANT
        ),
        breach_count=sum(
            1 for item in results if item.compliance_status is ComplianceStatus.BREACH
        ),
        plans_hash=plan_hash,
        results_hash=result_hash,
    )
    report = EvaluationReport(manifest=manifest, plans=plans, results=results)

    stage_parent = output_dir.parent
    stage_parent.mkdir(parents=True, exist_ok=True)
    stage_dir = Path(tempfile.mkdtemp(prefix=".evaluation-stage-", dir=str(stage_parent)))
    try:
        write_evaluation_outputs(report, stage_dir)
        if output_dir.exists() and any(output_dir.iterdir()):
            _replace_published_outputs(stage_dir, output_dir)
        else:
            _publish_staged(stage_dir, output_dir)
    finally:
        if stage_dir.exists():
            shutil.rmtree(stage_dir, ignore_errors=True)
    return report


def print_evaluation_summary(report: EvaluationReport) -> None:
    manifest = report.manifest
    print("covenant evaluation complete")
    print(f"plans={manifest.plan_count}")
    print(f"resolved={manifest.resolved_count}")
    print(f"unresolved={manifest.unresolved_count}")
    print(f"errors={manifest.error_count}")
    print(f"not_activated={manifest.not_activated_count}")
    print(f"compliant={manifest.compliant_count}")
    print(f"breach={manifest.breach_count}")


def report_to_json(report: EvaluationReport) -> str:
    return report.model_dump_json(indent=2) + "\n"
