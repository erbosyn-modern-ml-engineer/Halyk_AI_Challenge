"""I/O helpers for Stage 5D covenant outputs."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from halyk_agent.domain.authority.models import AuthorityDecision
from halyk_agent.domain.covenants.models import CovenantReport
from halyk_agent.domain.ids import sha256_text


class CovenantIOError(Exception):
    def __init__(self, message: str, *, code: str = "COVENANT_IO") -> None:
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


def load_authority_decisions(path: Path) -> tuple[AuthorityDecision, ...]:
    if not path.is_file():
        raise CovenantIOError(f"authority decisions missing: {path}", code="MISSING_DECISIONS")
    items: list[AuthorityDecision] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        items.append(AuthorityDecision.model_validate_json(line))
    return tuple(items)


def load_authority_manifest_hash(path: Path) -> str:
    if not path.is_file():
        raise CovenantIOError(f"authority manifest missing: {path}", code="MISSING_MANIFEST")
    return sha256_text(path.read_text(encoding="utf-8"))


def write_covenant_outputs(report: CovenantReport, output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "manifest": output_dir / "covenant_manifest.json",
        "definitions": output_dir / "covenant_definitions.jsonl",
        "failures": output_dir / "covenant_compile_failures.jsonl",
        "summary": output_dir / "covenant_summary.md",
    }
    _atomic_write(paths["manifest"], report.manifest.model_dump_json(indent=2) + "\n")
    _atomic_write(paths["definitions"], _jsonl(report.definitions))
    _atomic_write(paths["failures"], _jsonl(report.failures))
    _atomic_write(paths["summary"], render_summary_markdown(report))
    return paths


def render_summary_markdown(report: CovenantReport) -> str:
    m = report.manifest
    family_counts: dict[str, int] = {}
    comparator_counts: dict[str, int] = {}
    quantity_counts: dict[str, int] = {}
    for item in report.definitions:
        family_counts[item.family_id] = family_counts.get(item.family_id, 0) + 1
        comparator_counts[item.comparator.value] = (
            comparator_counts.get(item.comparator.value, 0) + 1
        )
        quantity_counts[item.metric_quantity_type.value] = (
            quantity_counts.get(item.metric_quantity_type.value, 0) + 1
        )
    lines = [
        "# Covenant compile summary (Stage 5D)",
        "",
        f"- scenarios: {m.scenario_count}",
        f"- cells: {m.cell_count}",
        f"- authoritative_covenant_docs: {m.authoritative_covenant_docs}",
        f"- definitions: {m.definition_count}",
        f"- failures: {m.failure_count}",
        f"- unsupported_count: {m.unsupported_count}",
        f"- evidence_spans: {m.evidence_span_count}",
        f"- compiler_version: `{m.compiler_version}`",
        f"- rule_version: `{m.rule_version}`",
        "",
        "## Formula families",
        "",
    ]
    for key, count in sorted(family_counts.items()):
        lines.append(f"- {key}: {count}")
    lines.extend(["", "## Comparators", ""])
    for key, count in sorted(comparator_counts.items()):
        lines.append(f"- {key}: {count}")
    lines.extend(["", "## Quantity types", ""])
    for key, count in sorted(quantity_counts.items()):
        lines.append(f"- {key}: {count}")
    if report.failures:
        lines.extend(["", "## Failures", ""])
        for failure in report.failures:
            lines.append(
                f"- {failure.scenario_id}/{failure.clause_id}: "
                f"{failure.status.value} — {failure.reason}"
            )
    lines.append("")
    return "\n".join(lines)
