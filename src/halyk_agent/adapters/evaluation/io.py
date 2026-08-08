"""I/O and integrity helpers for deterministic Stage 6 evaluation artifacts."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from halyk_agent.domain.covenant_evaluation.models import (
    EvaluationReport,
)
from halyk_agent.domain.covenants.models import CovenantDefinition
from halyk_agent.domain.ids import sha256_text
from halyk_agent.domain.transaction_taxonomy.engine import hash_taxonomy_models
from halyk_agent.domain.transaction_taxonomy.models import (
    CalculationInput,
    DefinitionReadinessEntry,
    SelectorCoverageEntry,
    TaxonomyManifest,
)


class EvaluationIOError(Exception):
    def __init__(self, message: str, *, code: str = "EVALUATION_IO") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(path)


def _jsonl(models: Iterable[Any]) -> str:
    lines = [
        json.dumps(model.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        for model in models
    ]
    return "\n".join(lines) + ("\n" if lines else "")


def hash_evaluation_models(models: Sequence[Any]) -> str:
    """Canonical Stage 6 hash; stable ordering is an explicit caller contract."""

    payload = [
        model.model_dump(mode="json") if hasattr(model, "model_dump") else model for model in models
    ]
    return sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))


def hash_covenant_definitions(definitions: Sequence[CovenantDefinition]) -> str:
    """Match Stage 5D CovenantManifest.definitions_hash exactly."""

    payload = [item.model_dump(mode="json") for item in definitions]
    text = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256_text(text)


def load_calculation_inputs(path: Path) -> tuple[CalculationInput, ...]:
    if not path.is_file():
        raise EvaluationIOError(
            f"calculation inputs missing: {path}",
            code="MISSING_CALCULATION_INPUTS",
        )
    items = [
        CalculationInput.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return tuple(items)


def _load_json_array(path: Path, *, code: str) -> list[Any]:
    if not path.is_file():
        raise EvaluationIOError(f"required JSON missing: {path}", code=code)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise EvaluationIOError(f"expected JSON array: {path}", code="ARTIFACT_SCHEMA")
    return payload


def load_selector_coverage(path: Path) -> tuple[SelectorCoverageEntry, ...]:
    payload = _load_json_array(path, code="MISSING_SELECTOR_COVERAGE")
    return tuple(SelectorCoverageEntry.model_validate(item) for item in payload)


def load_definition_readiness(path: Path) -> tuple[DefinitionReadinessEntry, ...]:
    payload = _load_json_array(path, code="MISSING_DEFINITION_READINESS")
    return tuple(DefinitionReadinessEntry.model_validate(item) for item in payload)


def load_taxonomy_manifest(path: Path) -> TaxonomyManifest:
    if not path.is_file():
        raise EvaluationIOError(
            f"Stage 5F manifest missing: {path}",
            code="MISSING_TAXONOMY_MANIFEST",
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    return TaxonomyManifest.model_validate(payload)


def verify_calculation_inputs_hash(
    manifest: TaxonomyManifest,
    inputs: tuple[CalculationInput, ...],
) -> None:
    actual = hash_taxonomy_models(inputs)
    if actual != manifest.calculation_inputs_hash:
        raise EvaluationIOError(
            "calculation_inputs.jsonl content hash does not match Stage 5F manifest",
            code="CALCULATION_INPUTS_HASH_MISMATCH",
        )
    if len(inputs) != manifest.calculation_input_count:
        raise EvaluationIOError(
            "calculation input count does not match Stage 5F manifest",
            code="CALCULATION_INPUTS_COUNT_MISMATCH",
        )


def write_evaluation_outputs(report: EvaluationReport, output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "manifest": output_dir / "evaluation_manifest.json",
        "plans": output_dir / "evaluation_plans.jsonl",
        "results": output_dir / "covenant_evaluations.jsonl",
        "summary": output_dir / "evaluation_summary.md",
    }
    _atomic_write(paths["manifest"], report.manifest.model_dump_json(indent=2) + "\n")
    _atomic_write(paths["plans"], _jsonl(report.plans))
    _atomic_write(paths["results"], _jsonl(report.results))
    _atomic_write(paths["summary"], render_evaluation_summary(report))
    return paths


def render_evaluation_summary(report: EvaluationReport) -> str:
    manifest = report.manifest
    lines = [
        "# Covenant evaluation summary (Stage 6)",
        "",
        f"- plans: {manifest.plan_count}",
        f"- results: {manifest.result_count}",
        f"- resolved: {manifest.resolved_count}",
        f"- unresolved: {manifest.unresolved_count}",
        f"- errors: {manifest.error_count}",
        f"- not_activated: {manifest.not_activated_count}",
        f"- compliant: {manifest.compliant_count}",
        f"- breach: {manifest.breach_count}",
        f"- schema_version: `{manifest.schema_version}`",
        f"- algorithm_version: `{manifest.algorithm_version}`",
        "",
        "## Non-resolved definitions",
        "",
    ]
    non_resolved = [result for result in report.results if result.status.value != "RESOLVED"]
    if not non_resolved:
        lines.append("- none")
    else:
        for result in non_resolved:
            issue_codes = ", ".join(issue.code for issue in result.issues) or "none"
            lines.append(
                f"- `{result.scenario_id}/{result.clause_id}` {result.status.value}: {issue_codes}"
            )
    lines.append("")
    return "\n".join(lines)
