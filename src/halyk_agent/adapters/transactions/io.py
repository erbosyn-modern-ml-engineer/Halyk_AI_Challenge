"""I/O helpers for Stage 5F transaction taxonomy outputs."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from halyk_agent.domain.evidence import EvidenceSpan
from halyk_agent.domain.fact_extraction.engine import hash_fact_models
from halyk_agent.domain.fact_extraction.models import FactRecord, FactRequirementResult
from halyk_agent.domain.ids import sha256_text
from halyk_agent.domain.routing.models import TransactionEntityLink
from halyk_agent.domain.transaction_taxonomy.models import TaxonomyReport


class TransactionIOError(Exception):
    def __init__(self, message: str, *, code: str = "TXN_IO") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(path)


def _jsonl(models: Iterable[Any]) -> str:
    lines: list[str] = []
    for model in models:
        if hasattr(model, "model_dump"):
            lines.append(
                json.dumps(model.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
            )
        else:
            lines.append(json.dumps(model, ensure_ascii=False, sort_keys=True))
    return "\n".join(lines) + ("\n" if lines else "")


def load_json_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise TransactionIOError(f"manifest missing: {path}", code="MISSING_MANIFEST")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TransactionIOError(f"manifest is not an object: {path}", code="MANIFEST_SCHEMA")
    return payload


def manifest_file_hash(path: Path) -> str:
    return sha256_text(path.read_text(encoding="utf-8"))


def load_transaction_links(path: Path) -> tuple[TransactionEntityLink, ...]:
    if not path.is_file():
        raise TransactionIOError(f"transaction links missing: {path}", code="MISSING_LINKS")
    items: list[TransactionEntityLink] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        items.append(TransactionEntityLink.model_validate_json(line))
    items.sort(key=lambda item: (item.row_index, item.txn_id))
    return tuple(items)


def load_accepted_facts(path: Path) -> tuple[FactRecord, ...]:
    if not path.is_file():
        raise TransactionIOError(f"accepted facts missing: {path}", code="MISSING_FACTS")
    items: list[FactRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        items.append(FactRecord.model_validate_json(line))
    items.sort(key=lambda item: (item.scenario_id, item.fact_kind.value, item.fact_id))
    return tuple(items)


def load_accepted_facts_file_order(path: Path) -> tuple[FactRecord, ...]:
    """Load accepted facts in on-disk order (required for manifest hash verification)."""
    if not path.is_file():
        raise TransactionIOError(f"accepted facts missing: {path}", code="MISSING_FACTS")
    items: list[FactRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        items.append(FactRecord.model_validate_json(line))
    return tuple(items)


def load_fact_requirement_results(path: Path) -> tuple[FactRequirementResult, ...]:
    if not path.is_file():
        raise TransactionIOError(
            f"fact requirement results missing: {path}", code="MISSING_FACT_RESULTS"
        )
    items: list[FactRequirementResult] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        items.append(FactRequirementResult.model_validate_json(line))
    return tuple(items)


def load_fact_evidence_spans(path: Path) -> tuple[EvidenceSpan, ...]:
    if not path.is_file():
        raise TransactionIOError(f"fact evidence missing: {path}", code="MISSING_FACT_EVIDENCE")
    items: list[EvidenceSpan] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        items.append(EvidenceSpan.model_validate_json(line))
    return tuple(items)


def verify_fact_artifact_hashes(
    *,
    facts_manifest: dict[str, Any],
    accepted_facts: tuple[FactRecord, ...],
    requirement_results: tuple[FactRequirementResult, ...] | None = None,
    evidence_spans: tuple[EvidenceSpan, ...] | None = None,
) -> None:
    """
    Fail closed when consumed Stage 5E artifact content diverges from manifest hashes.

    Hashes are recomputed with the same Stage 5E algorithm (model dumps / span ids).
    """
    expected_facts = facts_manifest.get("accepted_facts_hash")
    if not expected_facts:
        raise TransactionIOError(
            "facts manifest missing accepted_facts_hash",
            code="FACT_ARTIFACT_HASH_MISSING",
        )
    actual_facts = hash_fact_models(accepted_facts)
    if actual_facts != expected_facts:
        raise TransactionIOError(
            "accepted_facts.jsonl content hash does not match fact_extraction_manifest",
            code="FACT_ARTIFACT_HASH_MISMATCH",
        )

    expected_results = facts_manifest.get("requirement_results_hash")
    if expected_results:
        if requirement_results is None:
            raise TransactionIOError(
                "requirement results required for hash verification",
                code="FACT_ARTIFACT_HASH_MISSING",
            )
        actual_results = hash_fact_models(requirement_results)
        if actual_results != expected_results:
            raise TransactionIOError(
                "fact_requirement_results.jsonl content hash does not match manifest",
                code="FACT_ARTIFACT_HASH_MISMATCH",
            )

    expected_evidence = facts_manifest.get("evidence_hash")
    if expected_evidence:
        if evidence_spans is None:
            raise TransactionIOError(
                "fact evidence required for hash verification",
                code="FACT_ARTIFACT_HASH_MISSING",
            )
        actual_evidence = sha256_text("|".join(sorted(span.id for span in evidence_spans)))
        if actual_evidence != expected_evidence:
            raise TransactionIOError(
                "fact_evidence.jsonl content hash does not match manifest",
                code="FACT_ARTIFACT_HASH_MISMATCH",
            )


def write_taxonomy_outputs(report: TaxonomyReport, output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "taxonomy": output_dir / "transaction_taxonomy.jsonl",
        "adjustments": output_dir / "transaction_adjustments.jsonl",
        "calculation_inputs": output_dir / "calculation_inputs.jsonl",
        "derived_inputs": output_dir / "derived_inputs.jsonl",
        "conflicts": output_dir / "transaction_conflicts.jsonl",
        "unresolved": output_dir / "transaction_unresolved.jsonl",
        "selector_coverage": output_dir / "selector_coverage.json",
        "definition_readiness": output_dir / "definition_readiness.json",
        "fact_consumption": output_dir / "fact_consumption.jsonl",
        "related_parties": output_dir / "qualifying_related_parties.json",
        "manifest": output_dir / "stage5f_manifest.json",
        "summary": output_dir / "stage5f_summary.md",
    }
    _atomic_write(paths["taxonomy"], _jsonl(report.classified))
    _atomic_write(paths["adjustments"], _jsonl(report.adjustments))
    _atomic_write(paths["calculation_inputs"], _jsonl(report.calculation_inputs))
    _atomic_write(paths["derived_inputs"], _jsonl(report.derived_inputs))
    _atomic_write(paths["conflicts"], _jsonl(report.conflicts))
    _atomic_write(paths["unresolved"], _jsonl(report.unresolved))
    _atomic_write(
        paths["selector_coverage"],
        json.dumps(
            [e.model_dump(mode="json") for e in report.selector_coverage],
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    _atomic_write(
        paths["definition_readiness"],
        json.dumps(
            [e.model_dump(mode="json") for e in report.definition_readiness],
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    _atomic_write(paths["fact_consumption"], _jsonl(report.fact_consumption))
    _atomic_write(
        paths["related_parties"],
        json.dumps(list(report.qualifying_related_parties), ensure_ascii=False, indent=2) + "\n",
    )
    _atomic_write(paths["manifest"], report.manifest.model_dump_json(indent=2) + "\n")
    _atomic_write(paths["summary"], render_summary_markdown(report))
    return paths


def render_summary_markdown(report: TaxonomyReport) -> str:
    m = report.manifest
    lines = [
        "# Stage 5F transaction taxonomy summary",
        "",
        f"- ledger_rows: {m.ledger_row_count}",
        f"- scenario_linked: {m.scenario_linked_count}",
        f"- routing_noise: {m.routing_noise_count}",
        f"- classified: {m.classified_count}",
        f"- unresolved: {m.unresolved_count}",
        f"- conflicts: {m.conflict_count}",
        f"- calculation_inputs: {m.calculation_input_count}",
        f"- derived_inputs: {m.derived_input_count}",
        f"- adjustments: {m.adjustment_event_count}",
        f"- selectors READY/TRUE_ZERO/UNRESOLVED: "
        f"{m.selector_ready_count}/{m.selector_true_zero_count}/{m.selector_unresolved_count}"
        f" (total {m.selector_count})",
        f"- definitions READY/UNRESOLVED: "
        f"{m.definition_ready_count}/{m.definition_unresolved_count}",
        f"- facts_consumed: {m.facts_consumed_count}/{m.accepted_facts_count}",
        f"- related_party TRUE/FALSE/UNKNOWN: "
        f"{m.related_party_true_count}/{m.related_party_false_count}/{m.related_party_unknown_count}",
        f"- schema_version: `{m.schema_version}`",
        f"- algorithm_version: `{m.algorithm_version}`",
        "",
        "## Primary categories",
        "",
    ]
    for cat, count in sorted(m.category_counts.items()):
        lines.append(f"- `{cat}`: {count}")
    lines.extend(["", "## Selector memberships", ""])
    for cat, count in sorted(m.membership_counts.items()):
        lines.append(f"- `{cat}`: {count}")
    lines.extend(["", "## Methods", ""])
    for method, count in sorted(m.method_counts.items()):
        lines.append(f"- `{method}`: {count}")
    lines.extend(["", "## Definition readiness (unresolved)", ""])
    for entry in report.definition_readiness:
        if entry.status.value == "UNRESOLVED":
            lines.append(
                f"- `{entry.definition_id}` ({entry.scenario_id}): "
                f"{', '.join(entry.unresolved_selectors) or entry.reason_code}"
            )
    lines.append("")
    return "\n".join(lines)


def definitions_compat_hash(path: Path) -> str:
    """Stable hash for covenant definitions file bytes."""
    return sha256_text(path.read_text(encoding="utf-8"))
