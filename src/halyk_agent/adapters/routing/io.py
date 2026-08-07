"""I/O helpers for Stage 5B routing outputs."""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from halyk_agent.domain.evidence import EvidenceSpan
from halyk_agent.domain.routing.models import (
    ConflictKind,
    DiagnosticSeverity,
    LedgerRow,
    RoutingReport,
)


class RoutingIOError(Exception):
    def __init__(self, message: str, *, code: str = "ROUTING_IO") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(path)


def load_ledger_csv(path: Path) -> tuple[LedgerRow, ...]:
    """Load primary transaction ledger into typed rows."""
    if not path.is_file():
        raise RoutingIOError(f"ledger not found: {path}", code="MISSING_LEDGER")
    rows: list[LedgerRow] = []
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {
            "txn_id",
            "date",
            "account_id",
            "counterparty",
            "description",
            "amount",
            "currency",
        }
        if reader.fieldnames is None or not required.issubset(set(reader.fieldnames)):
            raise RoutingIOError(
                f"ledger missing required columns: {sorted(required)}",
                code="LEDGER_SCHEMA",
            )
        source = path.as_posix()
        for index, raw in enumerate(reader):
            rows.append(
                LedgerRow(
                    row_index=index,
                    txn_id=str(raw["txn_id"]),
                    date=str(raw["date"]),
                    account_id=str(raw["account_id"]),
                    counterparty=str(raw["counterparty"]),
                    description=str(raw.get("description") or ""),
                    amount=str(raw["amount"]),
                    currency=str(raw["currency"]),
                    ledger_source_file=source,
                )
            )
    return tuple(rows)


def load_evidence_catalogue(path: Path) -> tuple[EvidenceSpan, ...]:
    if not path.is_file():
        return ()
    spans: list[EvidenceSpan] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        spans.append(EvidenceSpan.model_validate_json(line))
    spans.sort(key=lambda item: item.id)
    return tuple(spans)


def load_template_answers(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or "answers" not in payload:
        raise RoutingIOError("submission template missing answers", code="TEMPLATE_SCHEMA")
    answers = payload["answers"]
    if not isinstance(answers, dict):
        raise RoutingIOError(
            "submission template answers must be an object", code="TEMPLATE_SCHEMA"
        )
    return answers


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


def write_routing_outputs(report: RoutingReport, output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "manifest": output_dir / "routing_manifest.json",
        "scenario_routes": output_dir / "scenario_routes.jsonl",
        "document_links": output_dir / "document_links.jsonl",
        "transaction_links": output_dir / "transaction_links.jsonl",
        "entity_conflicts": output_dir / "entity_conflicts.jsonl",
        "summary": output_dir / "routing_summary.md",
    }
    _atomic_write(
        paths["manifest"],
        report.manifest.model_dump_json(indent=2) + "\n",
    )
    _atomic_write(paths["scenario_routes"], _jsonl(report.scenario_routes))
    _atomic_write(paths["document_links"], _jsonl(report.document_links))
    _atomic_write(paths["transaction_links"], _jsonl(report.transaction_links))
    _atomic_write(paths["entity_conflicts"], _jsonl(report.conflicts))
    _atomic_write(paths["summary"], render_summary_markdown(report))
    return paths


def render_summary_markdown(report: RoutingReport) -> str:
    m = report.manifest
    account_dist = {route.scenario_id: len(route.account_ids) for route in report.scenario_routes}
    lines = [
        "# Routing summary (Stage 5B)",
        "",
        f"- scenarios: {m.scenario_count}",
        f"- template_cells: {m.template_cell_count}",
        f"- ledger_rows: {m.ledger_row_count}",
        f"- scenario_transactions: {m.scenario_transaction_count}",
        f"- transaction_links: {m.transaction_link_count}",
        f"- documents_resolved: {m.resolved_document_count}",
        f"- documents_unresolved: {m.unresolved_document_count}",
        f"- multi_scenario_documents: {m.multi_scenario_document_count}",
        f"- conflicts: {m.conflict_count}",
        f"- routing_algorithm_version: `{m.routing_algorithm_version}`",
        f"- normalization_version: `{m.normalization_version}`",
        "",
        "## Accounts per scenario",
        "",
    ]
    for route in report.scenario_routes:
        accounts = ", ".join(route.account_ids) if route.account_ids else "(none)"
        lines.append(
            f"- `{route.scenario_id}`: accounts=[{accounts}] "
            f"txns={route.transaction_count} docs={len(route.document_ids)}"
        )
    lines.extend(["", "## Account count distribution", ""])
    for scenario_id, count in sorted(account_dist.items()):
        lines.append(f"- `{scenario_id}`: {count}")
    lines.append("")
    return "\n".join(lines)


def has_structural_failure(report: RoutingReport) -> bool:
    """Strict-mode structural failures that make a scenario unusable."""
    for conflict in report.conflicts:
        if conflict.severity is DiagnosticSeverity.ERROR and conflict.kind in {
            ConflictKind.SCENARIO_WITHOUT_ACCOUNT,
            ConflictKind.DUPLICATE_TXN_ID,
            ConflictKind.TRANSACTION_ACCOUNT_CONFLICT,
        }:
            return True
    return False
