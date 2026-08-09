"""Transaction taxonomy application service (Stage 5F)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from halyk_agent.adapters.facts.io import load_covenant_definitions
from halyk_agent.adapters.routing.io import load_ledger_csv
from halyk_agent.adapters.transactions.io import (
    TransactionIOError,
    load_accepted_facts,
    load_accepted_facts_file_order,
    load_fact_evidence_spans,
    load_fact_requirement_results,
    load_json_manifest,
    load_transaction_links,
    manifest_file_hash,
    verify_fact_artifact_hashes,
    write_taxonomy_outputs,
)
from halyk_agent.config import Settings, get_settings
from halyk_agent.domain.transaction_taxonomy.engine import run_transaction_taxonomy
from halyk_agent.domain.transaction_taxonomy.models import TaxonomyReport
from halyk_agent.domain.transaction_taxonomy.semantic_classifier import classify_unresolved_rows


class TransactionServiceError(Exception):
    def __init__(self, message: str, *, code: str = "TXN_ERROR") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def assert_no_gt_access(path: Path) -> None:
    name = path.name.casefold()
    if "ground_truth" in name or name.endswith("answer_key.json"):
        raise TransactionServiceError(
            "ground truth access forbidden in Stage 5F",
            code="GT_FORBIDDEN",
        )


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
        backup_dir = Path(tempfile.mkdtemp(prefix=".txn-prev-", dir=str(output_dir.parent)))
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


def _resolve_file(directory: Path, name: str) -> Path:
    direct = directory / name
    if direct.is_file():
        return direct
    raise TransactionServiceError(f"missing required file: {direct}", code="MISSING_INPUT")


def _ledger_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def transactions_from_paths(
    *,
    routing_dir: Path,
    covenants_dir: Path,
    facts_dir: Path,
    ledger_path: Path,
    output_dir: Path,
    overwrite: bool = False,
    settings: Settings | None = None,
) -> TaxonomyReport:
    for path in (routing_dir, covenants_dir, facts_dir, ledger_path, output_dir):
        assert_no_gt_access(path)

    if output_dir.exists() and any(output_dir.iterdir()) and not overwrite:
        raise TransactionServiceError(
            f"output exists (pass --overwrite): {output_dir}",
            code="OUTPUT_EXISTS",
        )

    routing_manifest_path = _resolve_file(routing_dir, "routing_manifest.json")
    covenant_manifest_path = _resolve_file(covenants_dir, "covenant_manifest.json")
    facts_manifest_path = _resolve_file(facts_dir, "fact_extraction_manifest.json")
    links_path = _resolve_file(routing_dir, "transaction_links.jsonl")
    definitions_path = _resolve_file(covenants_dir, "covenant_definitions.jsonl")
    accepted_path = _resolve_file(facts_dir, "accepted_facts.jsonl")
    results_path = _resolve_file(facts_dir, "fact_requirement_results.jsonl")
    evidence_path = _resolve_file(facts_dir, "fact_evidence.jsonl")

    routing_manifest = load_json_manifest(routing_manifest_path)
    covenant_manifest = load_json_manifest(covenant_manifest_path)
    facts_manifest = load_json_manifest(facts_manifest_path)

    ledger_sha = _ledger_sha256(ledger_path)
    expected_ledger = (routing_manifest.get("parsed_input_identity") or {}).get(
        "ledger_source_sha256"
    ) or routing_manifest.get("ledger_source_sha256")
    if not expected_ledger:
        raise TransactionServiceError(
            "routing manifest missing ledger_source_sha256",
            code="LEDGER_IDENTITY_MISSING",
        )
    if expected_ledger != ledger_sha:
        raise TransactionServiceError(
            "ledger SHA does not match routing manifest ledger_source_sha256",
            code="LEDGER_MISMATCH",
        )

    cov_auth = covenant_manifest.get("authority_manifest_hash")
    fact_auth = facts_manifest.get("authority_manifest_hash")
    if not cov_auth or not fact_auth:
        raise TransactionServiceError(
            "covenants/facts authority_manifest_hash missing",
            code="AUTHORITY_IDENTITY_MISSING",
        )
    if cov_auth != fact_auth:
        raise TransactionServiceError(
            "facts/covenants authority_manifest_hash mismatch",
            code="AUTHORITY_MISMATCH",
        )

    try:
        # Integrity first — never publish Stage 5F outputs on doctored upstream facts.
        facts_file_order = load_accepted_facts_file_order(accepted_path)
        requirement_results = load_fact_requirement_results(results_path)
        evidence_spans = load_fact_evidence_spans(evidence_path)
        verify_fact_artifact_hashes(
            facts_manifest=facts_manifest,
            accepted_facts=facts_file_order,
            requirement_results=requirement_results,
            evidence_spans=evidence_spans,
        )

        ledger_rows = load_ledger_csv(ledger_path)
        links = load_transaction_links(links_path)
        definitions = load_covenant_definitions(definitions_path)
        facts = load_accepted_facts(accepted_path)
    except TransactionIOError as exc:
        raise TransactionServiceError(exc.message, code=exc.code) from exc

    routing_scenarios = {link.scenario_id for link in links if link.scenario_id}
    covenant_scenarios = {definition.scenario_id for definition in definitions}
    facts_scenarios = {fact.scenario_id for fact in facts}
    if not covenant_scenarios.issubset(routing_scenarios):
        raise TransactionServiceError(
            "covenant scenario universe is not a subset of routing scenarios",
            code="SCENARIO_UNIVERSE_MISMATCH",
        )
    if not facts_scenarios.issubset(routing_scenarios):
        raise TransactionServiceError(
            "accepted facts scenario universe incompatible with routing",
            code="FACTS_SCENARIO_MISMATCH",
        )

    resolved_settings = settings or get_settings()
    semantic = classify_unresolved_rows(ledger_rows, links, settings=resolved_settings)

    report = run_transaction_taxonomy(
        ledger_rows=ledger_rows,
        transaction_links=links,
        definitions=definitions,
        accepted_facts=facts,
        ledger_source_sha256=ledger_sha,
        routing_manifest_hash=manifest_file_hash(routing_manifest_path),
        covenant_manifest_hash=manifest_file_hash(covenant_manifest_path),
        facts_manifest_hash=manifest_file_hash(facts_manifest_path),
        fact_requirement_results=requirement_results,
        classification_overrides=semantic.overrides,
        semantic_model_calls=semantic.model_calls,
    )

    stage_parent = output_dir.parent
    stage_parent.mkdir(parents=True, exist_ok=True)
    stage_dir = Path(tempfile.mkdtemp(prefix=".txn-stage-", dir=str(stage_parent)))
    try:
        write_taxonomy_outputs(report, stage_dir)
        semantic_path = stage_dir / "semantic_classification.jsonl"
        semantic_text = "\n".join(
            json.dumps(item, ensure_ascii=False, sort_keys=True) for item in semantic.diagnostics
        )
        if semantic_text:
            semantic_text += "\n"
        semantic_path.write_text(semantic_text, encoding="utf-8", newline="\n")
        if output_dir.exists() and any(output_dir.iterdir()):
            _replace_published_outputs(stage_dir, output_dir)
        else:
            _publish_staged(stage_dir, output_dir)
    finally:
        if stage_dir.exists():
            shutil.rmtree(stage_dir, ignore_errors=True)
    return report


def print_taxonomy_summary(report: TaxonomyReport) -> None:
    m = report.manifest
    print("transaction taxonomy complete")
    print(f"ledger_rows={m.ledger_row_count}")
    print(f"scenario_linked={m.scenario_linked_count}")
    print(f"routing_noise={m.routing_noise_count}")
    print(f"classified={m.classified_count}")
    print(f"unresolved={m.unresolved_count}")
    print(f"conflicts={m.conflict_count}")
    print(f"calculation_inputs={m.calculation_input_count}")
    print(f"derived_inputs={m.derived_input_count}")
    print(
        "selectors="
        f"READY:{m.selector_ready_count}/"
        f"TRUE_ZERO:{m.selector_true_zero_count}/"
        f"UNRESOLVED:{m.selector_unresolved_count}/"
        f"total:{m.selector_count}"
    )
    print(
        f"definitions=READY:{m.definition_ready_count}/UNRESOLVED:{m.definition_unresolved_count}"
    )
    print(f"facts_consumed={m.facts_consumed_count}/{m.accepted_facts_count}")
    print(
        "related_party="
        f"TRUE:{m.related_party_true_count}/"
        f"FALSE:{m.related_party_false_count}/"
        f"UNKNOWN:{m.related_party_unknown_count}"
    )


def report_to_json(report: TaxonomyReport) -> str:
    return report.model_dump_json(indent=2) + "\n"
