"""Idempotent finalization: candidate + visuals → authoritative ParseResult."""

from __future__ import annotations

from collections.abc import Sequence

from halyk_agent.adapters.parsing.post_parse_gate import (
    PageQualitySummary,
    PostParseGateResult,
    apply_post_parse_quality_gate,
)
from halyk_agent.domain.page_quality import PageVisualSignals
from halyk_agent.domain.parsing import (
    CanonicalDocument,
    ParseAttempt,
    ParseResult,
    ParseStatus,
    QualityDecision,
)


def finalize_canonical_parse(
    candidate: CanonicalDocument,
    *,
    page_visuals: Sequence[PageVisualSignals] | None = None,
) -> PostParseGateResult:
    """Run the authoritative post-parse gate once (idempotent on already-gated docs)."""
    return apply_post_parse_quality_gate(candidate, page_visuals=page_visuals)


def quality_decision_for(status: ParseStatus) -> QualityDecision:
    if status is ParseStatus.SUCCESS:
        return QualityDecision.ACCEPT
    if status is ParseStatus.PARTIAL:
        return QualityDecision.HUMAN_REVIEW_REQUIRED
    if status in {ParseStatus.ENCRYPTED, ParseStatus.UNSUPPORTED}:
        return QualityDecision.REJECT
    return QualityDecision.HUMAN_REVIEW_REQUIRED


def to_authoritative_parse_result(
    candidate: CanonicalDocument,
    *,
    page_visuals: Sequence[PageVisualSignals] | None = None,
    duration_ms: int = 0,
    cache_hit: bool = False,
    precomputed: PostParseGateResult | None = None,
) -> tuple[ParseResult, PageQualitySummary]:
    """Gate a candidate and wrap as ParseResult (single gate application)."""
    gated = precomputed or finalize_canonical_parse(candidate, page_visuals=page_visuals)
    document = gated.document
    attempt = ParseAttempt(
        parser=document.parser,
        status=document.status,
        metrics=document.metrics,
        warnings=list(document.warnings),
        duration_ms=duration_ms,
    )
    result = ParseResult(
        artifact_id=document.artifact_id,
        selected_document=document,
        attempts=[attempt],
        quality_decision=quality_decision_for(document.status),
        cache_hit=cache_hit,
    )
    return result, gated.summary
