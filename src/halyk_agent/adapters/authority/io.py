"""I/O helpers for Stage 5C authority outputs."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from halyk_agent.domain.authority.models import AuthorityReport, AuthorityStatus
from halyk_agent.domain.ids import sha256_text
from halyk_agent.domain.routing.models import DocumentEntityLink, RoutingManifest


class AuthorityIOError(Exception):
    def __init__(self, message: str, *, code: str = "AUTHORITY_IO") -> None:
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


def load_routing_manifest(path: Path) -> RoutingManifest:
    if not path.is_file():
        raise AuthorityIOError(f"routing manifest missing: {path}", code="MISSING_MANIFEST")
    return RoutingManifest.model_validate_json(path.read_text(encoding="utf-8"))


def load_document_links(path: Path) -> tuple[DocumentEntityLink, ...]:
    if not path.is_file():
        raise AuthorityIOError(f"document links missing: {path}", code="MISSING_LINKS")
    links: list[DocumentEntityLink] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        links.append(DocumentEntityLink.model_validate_json(line))
    return tuple(sorted(links, key=lambda item: item.document_id))


def load_identity_evidence_hash(path: Path) -> str:
    if not path.is_file():
        return ""
    return sha256_text(path.read_text(encoding="utf-8"))


def write_authority_outputs(report: AuthorityReport, output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "manifest": output_dir / "authority_manifest.json",
        "taxonomy": output_dir / "document_taxonomy.jsonl",
        "metadata": output_dir / "document_metadata.jsonl",
        "families": output_dir / "document_families.jsonl",
        "decisions": output_dir / "authority_decisions.jsonl",
        "conflicts": output_dir / "authority_conflicts.jsonl",
        "evidence": output_dir / "authority_evidence.jsonl",
        "summary": output_dir / "authority_summary.md",
    }
    _atomic_write(paths["manifest"], report.manifest.model_dump_json(indent=2) + "\n")
    _atomic_write(paths["taxonomy"], _jsonl(report.classifications))
    _atomic_write(paths["metadata"], _jsonl(report.metadata))
    _atomic_write(paths["families"], _jsonl(report.families))
    _atomic_write(paths["decisions"], _jsonl(report.decisions))
    _atomic_write(paths["conflicts"], _jsonl(report.conflicts))
    _atomic_write(paths["evidence"], _jsonl(report.evidence))
    _atomic_write(paths["summary"], render_summary_markdown(report))
    return paths


def render_summary_markdown(report: AuthorityReport) -> str:
    m = report.manifest
    type_counts: dict[str, int] = {}
    for item in report.classifications:
        type_counts[item.document_type.value] = type_counts.get(item.document_type.value, 0) + 1
    lines = [
        "# Authority summary (Stage 5C)",
        "",
        f"- documents: {m.document_count}",
        f"- classified: {m.classified_count}",
        f"- unknown: {m.unknown_count}",
        f"- decisions: {m.decision_count}",
        f"- conflicts: {m.conflict_count}",
        f"- missing_authority: {m.missing_authority_count}",
        f"- families: {m.family_count}",
        f"- evidence: {m.evidence_count}",
        f"- taxonomy_rule_version: `{m.taxonomy_rule_version}`",
        f"- authority_rule_version: `{m.authority_rule_version}`",
        "",
        "## Document types",
        "",
    ]
    for key in sorted(type_counts):
        lines.append(f"- `{key}`: {type_counts[key]}")
    lines.extend(["", "## Authority decisions by status", ""])
    status_counts: dict[str, int] = {}
    for decision in report.decisions:
        status_counts[decision.status.value] = status_counts.get(decision.status.value, 0) + 1
    for key in sorted(status_counts):
        lines.append(f"- `{key}`: {status_counts[key]}")
    lines.append("")
    return "\n".join(lines)


def has_structural_failure(report: AuthorityReport) -> bool:
    return any(d.status is AuthorityStatus.UNRESOLVED for d in report.decisions)
