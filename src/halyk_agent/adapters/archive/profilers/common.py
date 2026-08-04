"""Shared schema profiling helpers."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from halyk_agent.domain.datasets import (
    ColumnProfile,
    PrimitiveType,
    SemanticCandidate,
    SemanticType,
)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

_SEMANTIC_PATTERNS: list[tuple[SemanticType, tuple[str, ...], float]] = [
    (SemanticType.TRANSACTION_ID, ("transaction_id", "txn_id", "tx_id"), 0.9),
    (SemanticType.DOCUMENT_ID, ("document_id", "doc_id"), 0.85),
    (SemanticType.CASE_ID, ("case_id", "caseid"), 0.9),
    (SemanticType.ENTITY_ID, ("entity_id", "party_id", "customer_id"), 0.8),
    (SemanticType.COUNTERPARTY_ID, ("counterparty_id", "counterparty"), 0.85),
    (SemanticType.CONTRACT_ID, ("contract_id", "agreement_id"), 0.85),
    (SemanticType.INVOICE_ID, ("invoice_id", "invoice"), 0.8),
    (SemanticType.AMOUNT, ("amount", "sum", "value", "total"), 0.8),
    (SemanticType.CURRENCY, ("currency", "ccy", "curr"), 0.9),
    (SemanticType.STATUS, ("status", "state"), 0.75),
    (SemanticType.TRANSACTION_TYPE, ("transaction_type", "txn_type", "type"), 0.7),
    (SemanticType.OCCURRED_AT, ("occurred_at", "occurred", "event_time", "timestamp"), 0.85),
    (SemanticType.POSTED_AT, ("posted_at", "posted"), 0.85),
    (SemanticType.SETTLED_AT, ("settled_at", "settled"), 0.85),
    (SemanticType.REVERSAL_OF_ID, ("reversal_of_id", "reversal_of"), 0.9),
    (SemanticType.PARENT_TRANSACTION_ID, ("parent_transaction_id", "parent_txn_id"), 0.9),
    (SemanticType.DESCRIPTION, ("description", "memo", "narrative", "comment"), 0.7),
    (SemanticType.DECISION, ("decision", "verdict", "outcome"), 0.75),
    (SemanticType.EVIDENCE, ("evidence", "proof", "citation"), 0.75),
    (SemanticType.RECORD_ID, ("record_id", "id", "row_id"), 0.6),
]


def normalize_column_name(name: str) -> str:
    """Normalize a column name for matching and stable identity."""
    lowered = name.strip().lower().replace("-", "_")
    collapsed = _NON_ALNUM.sub("_", lowered).strip("_")
    return collapsed or "unnamed"


def truncate_example(value: str, max_length: int) -> str:
    """Truncate example values to a safe maximum length."""
    if len(value) <= max_length:
        return value
    return value[: max_length - 1] + "…"


def is_nullish(value: Any) -> bool:
    """Return True for empty / null-like sample values."""
    if value is None:
        return True
    return isinstance(value, str) and value.strip() == ""


def infer_primitive(values: list[Any]) -> PrimitiveType:
    """Infer a primitive type from non-null sample values."""
    non_null = [v for v in values if not is_nullish(v)]
    if not non_null:
        return PrimitiveType.NULL
    types: set[PrimitiveType] = set()
    for value in non_null:
        types.add(_infer_one(value))
    if len(types) == 1:
        return next(iter(types))
    if types <= {PrimitiveType.INTEGER, PrimitiveType.DECIMAL}:
        return PrimitiveType.DECIMAL
    if types <= {PrimitiveType.DATE, PrimitiveType.DATETIME}:
        return PrimitiveType.DATETIME
    return PrimitiveType.MIXED


def _infer_one(value: Any) -> PrimitiveType:
    if isinstance(value, bool):
        return PrimitiveType.BOOLEAN
    if isinstance(value, int) and not isinstance(value, bool):
        return PrimitiveType.INTEGER
    if isinstance(value, float):
        return PrimitiveType.DECIMAL
    if isinstance(value, Decimal):
        return PrimitiveType.DECIMAL
    if isinstance(value, datetime):
        return PrimitiveType.DATETIME
    if isinstance(value, date):
        return PrimitiveType.DATE
    text = str(value).strip()
    lowered = text.lower()
    if lowered in {"true", "false", "yes", "no", "y", "n"}:
        return PrimitiveType.BOOLEAN
    if re.fullmatch(r"[+-]?\d+", text):
        return PrimitiveType.INTEGER
    try:
        Decimal(text.replace(",", ""))
        if any(ch in text for ch in ".,"):
            return PrimitiveType.DECIMAL
        return PrimitiveType.INTEGER
    except InvalidOperation:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            datetime.strptime(text.replace("Z", ""), fmt.replace("Z", ""))
            return PrimitiveType.DATETIME
        except ValueError:
            continue
    try:
        datetime.strptime(text, "%Y-%m-%d")
        return PrimitiveType.DATE
    except ValueError:
        return PrimitiveType.STRING


def semantic_candidates_for(column_name: str) -> list[SemanticCandidate]:
    """Return deterministic semantic candidates from column naming signals."""
    normalized = normalize_column_name(column_name)
    hits: list[SemanticCandidate] = []
    for semantic, tokens, confidence in _SEMANTIC_PATTERNS:
        if normalized in tokens or any(token == normalized for token in tokens):
            hits.append(
                SemanticCandidate(
                    semantic_type=semantic,
                    confidence=confidence,
                    reasons=[f"column name matched token set for {semantic.value}"],
                )
            )
        elif any(token in normalized for token in tokens if len(token) >= 4):
            hits.append(
                SemanticCandidate(
                    semantic_type=semantic,
                    confidence=max(0.5, confidence - 0.15),
                    reasons=[f"column name contains token related to {semantic.value}"],
                )
            )
    if not hits:
        hits.append(
            SemanticCandidate(
                semantic_type=SemanticType.UNKNOWN,
                confidence=0.2,
                reasons=["no strong naming signal"],
            )
        )
    hits.sort(key=lambda item: (-item.confidence, item.semantic_type.value))
    return hits[:3]


def build_column_profile(
    *,
    name: str,
    position: int,
    values: list[Any],
    max_sample_value_length: int,
) -> ColumnProfile:
    """Build a ColumnProfile from sampled values."""
    null_count = sum(1 for value in values if is_nullish(value))
    non_null_values = [value for value in values if not is_nullish(value)]
    rendered = [truncate_example(str(value), max_sample_value_length) for value in non_null_values]
    distinct = sorted(set(rendered))
    examples = distinct[:3]
    minimum = min(distinct) if distinct else None
    maximum = max(distinct) if distinct else None
    return ColumnProfile(
        name=name if name.strip() else f"column_{position}",
        normalized_name=normalize_column_name(name if name.strip() else f"column_{position}"),
        position=position,
        primitive_type=infer_primitive(non_null_values),
        nullable=null_count > 0 or not values,
        sample_non_null_count=len(non_null_values),
        sample_null_count=null_count,
        sample_distinct_count=len(distinct),
        examples=examples,
        minimum=minimum,
        maximum=maximum,
        semantic_candidates=semantic_candidates_for(name),
    )
