"""Deterministic heuristic role classification for archive artifacts."""

from __future__ import annotations

from pathlib import Path

from halyk_agent.domain.datasets import (
    ArtifactFormat,
    ArtifactRole,
    SemanticType,
    TableProfile,
)

_TRANSACTION_SEMANTICS = {
    SemanticType.TRANSACTION_ID,
    SemanticType.AMOUNT,
    SemanticType.CURRENCY,
    SemanticType.STATUS,
    SemanticType.OCCURRED_AT,
    SemanticType.CONTRACT_ID,
    SemanticType.INVOICE_ID,
}

_CASE_KEYS = {"case_id", "question", "task", "decision_as_of", "subject"}
_SUBMISSION_TOKENS = {"submission", "answer", "output", "prediction", "result"}
_SCORING_TOKENS = {"evaluation", "scoring", "metric", "rules", "criteria"}
_DOC_FORMATS = {ArtifactFormat.PDF, ArtifactFormat.DOCX, ArtifactFormat.TXT, ArtifactFormat.IMAGE}


def classify_role(
    *,
    relative_path: str,
    format_: ArtifactFormat,
    table_profile: TableProfile | None,
) -> tuple[ArtifactRole, float, list[str]]:
    """Classify an artifact role with confidence and human-readable reasons."""
    path = Path(relative_path)
    stem = path.stem.lower()
    tokens = {part.lower() for part in path.parts} | set(stem.replace("-", "_").split("_"))
    reasons: list[str] = []

    if format_ is ArtifactFormat.ZIP:
        return ArtifactRole.NESTED_ARCHIVE, 0.95, ["nested ZIP archive (not recursively extracted)"]

    semantic_hits: set[SemanticType] = set()
    column_names: set[str] = set()
    if table_profile is not None:
        for column in table_profile.columns:
            column_names.add(column.normalized_name)
            for candidate in column.semantic_candidates:
                if (
                    candidate.semantic_type is not SemanticType.UNKNOWN
                    and candidate.confidence >= 0.6
                ):
                    semantic_hits.add(candidate.semantic_type)
        for sheet in table_profile.sheets:
            for column in sheet.columns:
                column_names.add(column.normalized_name)
                for candidate in column.semantic_candidates:
                    if (
                        candidate.semantic_type is not SemanticType.UNKNOWN
                        and candidate.confidence >= 0.6
                    ):
                        semantic_hits.add(candidate.semantic_type)

    txn_overlap = semantic_hits & _TRANSACTION_SEMANTICS
    if len(txn_overlap) >= 3:
        reasons.append(
            "table columns matched transaction semantics: "
            + ", ".join(sorted(item.value for item in txn_overlap))
        )
        return ArtifactRole.TRANSACTION_TABLE, min(0.95, 0.55 + 0.1 * len(txn_overlap)), reasons

    if column_names & _CASE_KEYS or tokens & _CASE_KEYS:
        matched = sorted((column_names | tokens) & _CASE_KEYS)
        reasons.append(f"case-definition signals: {', '.join(matched)}")
        return ArtifactRole.CASE_DEFINITION, 0.8, reasons

    if tokens & _SUBMISSION_TOKENS or column_names & _SUBMISSION_TOKENS:
        matched = sorted((tokens | column_names) & _SUBMISSION_TOKENS)
        reasons.append(f"submission-template signals: {', '.join(matched)}")
        return ArtifactRole.SUBMISSION_TEMPLATE, 0.8, reasons

    if tokens & _SCORING_TOKENS or column_names & _SCORING_TOKENS:
        matched = sorted((tokens | column_names) & _SCORING_TOKENS)
        reasons.append(f"scoring-rules signals: {', '.join(matched)}")
        return ArtifactRole.SCORING_RULES, 0.8, reasons

    if "metadata" in tokens or stem in {"meta", "metadata"}:
        return ArtifactRole.METADATA, 0.7, ["filename suggests metadata"]

    if format_ in _DOC_FORMATS:
        return ArtifactRole.DOCUMENT, 0.7, [f"document format {format_.value}"]

    if format_ in {
        ArtifactFormat.CSV,
        ArtifactFormat.JSON,
        ArtifactFormat.JSONL,
        ArtifactFormat.XLSX,
    }:
        reasons.append("tabular/structured format without strong role signals")
        return ArtifactRole.UNKNOWN, 0.35, reasons

    return ArtifactRole.UNKNOWN, 0.2, ["weak evidence; defaulting to UNKNOWN"]
