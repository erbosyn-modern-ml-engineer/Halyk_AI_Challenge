"""I/O helpers for Stage 5E fact extraction outputs."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from halyk_agent.adapters.covenants.io import (
    CovenantIOError,
    load_authority_decisions,
    load_authority_manifest_hash,
)
from halyk_agent.domain.covenants.models import CovenantDefinition
from halyk_agent.domain.fact_extraction.models import FactExtractionReport
from halyk_agent.domain.ids import sha256_text

# Re-export for app layer convenience
__all__ = [
    "FactIOError",
    "load_authority_decisions",
    "load_authority_manifest_hash",
    "load_covenant_definitions",
    "write_fact_outputs",
]


class FactIOError(Exception):
    def __init__(self, message: str, *, code: str = "FACT_IO") -> None:
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
        lines.append(json.dumps(model.model_dump(mode="json"), ensure_ascii=False, sort_keys=True))
    return "\n".join(lines) + ("\n" if lines else "")


def load_covenant_definitions(path: Path) -> tuple[CovenantDefinition, ...]:
    if not path.is_file():
        raise FactIOError(f"covenant definitions missing: {path}", code="MISSING_DEFINITIONS")
    items: list[CovenantDefinition] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        items.append(CovenantDefinition.model_validate_json(line))
    return tuple(items)


def write_fact_outputs(report: FactExtractionReport, output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "requirements": output_dir / "fact_requirements.jsonl",
        "candidates": output_dir / "fact_candidates.jsonl",
        "accepted": output_dir / "accepted_facts.jsonl",
        "rejected": output_dir / "rejected_facts.jsonl",
        "conflicts": output_dir / "fact_conflicts.jsonl",
        "model_calls": output_dir / "model_calls.jsonl",
        "evidence": output_dir / "fact_evidence.jsonl",
        "manifest": output_dir / "fact_extraction_manifest.json",
        "summary": output_dir / "fact_extraction_summary.md",
    }
    _atomic_write(paths["requirements"], _jsonl(report.requirements))
    _atomic_write(paths["candidates"], _jsonl(report.candidates))
    _atomic_write(paths["accepted"], _jsonl(report.accepted_facts))
    _atomic_write(paths["rejected"], _jsonl(report.rejected_facts))
    _atomic_write(paths["conflicts"], _jsonl(report.conflicts))
    _atomic_write(paths["model_calls"], _jsonl(report.model_calls))
    _atomic_write(paths["evidence"], _jsonl(report.spans))
    _atomic_write(paths["manifest"], report.manifest.model_dump_json(indent=2) + "\n")
    _atomic_write(paths["summary"], render_summary_markdown(report))
    return paths


def render_summary_markdown(report: FactExtractionReport) -> str:
    m = report.manifest
    kind_counts: dict[str, int] = {}
    for fact in report.accepted_facts:
        kind_counts[fact.fact_kind.value] = kind_counts.get(fact.fact_kind.value, 0) + 1
    lines = [
        "# Fact extraction summary (Stage 5E)",
        "",
        f"- scenarios: {m.scenario_count}",
        f"- requirements: {m.requirement_count}",
        f"- candidates: {m.candidate_count}",
        f"- accepted: {m.accepted_count}",
        f"- rejected: {m.rejected_count}",
        f"- unresolved: {m.unresolved_count}",
        f"- conflicts: {m.conflict_count}",
        f"- model_calls: {m.model_call_count}",
        f"- deterministic_accepted: {m.deterministic_accepted_count}",
        f"- llm_accepted: {m.llm_accepted_count}",
        f"- evidence_spans: {m.evidence_span_count}",
        f"- allow_network_models: {m.allow_network_models}",
        f"- schema_version: `{m.schema_version}`",
        "",
        "## Accepted by kind",
        "",
    ]
    for key, count in sorted(kind_counts.items()):
        lines.append(f"- {key}: {count}")
    if report.unresolved_requirement_ids:
        lines.extend(["", "## Unresolved requirements (sample)", ""])
        for rid in report.unresolved_requirement_ids[:20]:
            lines.append(f"- `{rid[:16]}…`")
    lines.append("")
    return "\n".join(lines)


def definitions_file_hash(path: Path) -> str:
    return sha256_text(path.read_text(encoding="utf-8"))


# Keep CovenantIOError import used for typing re-export consumers
_ = CovenantIOError
