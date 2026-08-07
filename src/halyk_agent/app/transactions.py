"""Transaction taxonomy application service (Stage 5F)."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from halyk_agent.adapters.facts.io import load_covenant_definitions
from halyk_agent.adapters.routing.io import load_ledger_csv
from halyk_agent.adapters.transactions.io import (
    TransactionIOError,
    load_accepted_facts,
    load_json_manifest,
    load_transaction_links,
    manifest_file_hash,
    write_taxonomy_outputs,
)
from halyk_agent.domain.transaction_taxonomy.engine import run_transaction_taxonomy
from halyk_agent.domain.transaction_taxonomy.models import TaxonomyReport


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

    routing_manifest = load_json_manifest(routing_manifest_path)
    covenant_manifest = load_json_manifest(covenant_manifest_path)
    facts_manifest = load_json_manifest(facts_manifest_path)

    ledger_sha = _ledger_sha256(ledger_path)
    expected_ledger = (routing_manifest.get("parsed_input_identity") or {}).get(
        "ledger_source_sha256"
    ) or routing_manifest.get("ledger_source_sha256")
    if expected_ledger and expected_ledger != ledger_sha:
        raise TransactionServiceError(
            "ledger SHA does not match routing manifest ledger_source_sha256",
            code="LEDGER_MISMATCH",
        )

    cov_auth = covenant_manifest.get("authority_manifest_hash")
    fact_auth = facts_manifest.get("authority_manifest_hash")
    if cov_auth and fact_auth and cov_auth != fact_auth:
        raise TransactionServiceError(
            "facts/covenants authority_manifest_hash mismatch",
            code="AUTHORITY_MISMATCH",
        )

    try:
        ledger_rows = load_ledger_csv(ledger_path)
        links = load_transaction_links(links_path)
        definitions = load_covenant_definitions(definitions_path)
        facts = load_accepted_facts(accepted_path)
    except TransactionIOError as exc:
        raise TransactionServiceError(exc.message, code=exc.code) from exc

    report = run_transaction_taxonomy(
        ledger_rows=ledger_rows,
        transaction_links=links,
        definitions=definitions,
        accepted_facts=facts,
        ledger_source_sha256=ledger_sha,
        routing_manifest_hash=manifest_file_hash(routing_manifest_path),
        covenant_manifest_hash=manifest_file_hash(covenant_manifest_path),
        facts_manifest_hash=manifest_file_hash(facts_manifest_path),
    )

    stage_parent = output_dir.parent
    stage_parent.mkdir(parents=True, exist_ok=True)
    stage_dir = Path(tempfile.mkdtemp(prefix=".txn-stage-", dir=str(stage_parent)))
    try:
        write_taxonomy_outputs(report, stage_dir)
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
    print(f"selectors_supported={m.selector_supported_count}/{m.selector_count}")
    print(f"facts_consumed={m.facts_consumed_count}/{m.accepted_facts_count}")
    print(
        "related_party="
        f"TRUE:{m.related_party_true_count}/"
        f"FALSE:{m.related_party_false_count}/"
        f"UNKNOWN:{m.related_party_unknown_count}"
    )


def report_to_json(report: TaxonomyReport) -> str:
    return report.model_dump_json(indent=2) + "\n"
