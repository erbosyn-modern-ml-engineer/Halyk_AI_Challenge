"""Deterministic parse quality gate and routing signals."""

from __future__ import annotations

from dataclasses import dataclass, field

from halyk_agent.contracts.parsing import ParseQualityReport
from halyk_agent.domain.parsing import (
    CanonicalDocument,
    ParseStatus,
    ParseWarning,
    ParseWarningCode,
    QualityDecision,
)


@dataclass(frozen=True, slots=True)
class QualityThresholds:
    """Configurable quality thresholds."""

    min_total_characters: int = 1
    max_empty_page_ratio: float = 0.5
    max_replacement_character_ratio: float = 0.05
    min_alphanumeric_character_ratio: float = 0.2
    max_control_character_count: int = 100
    max_duplicate_line_ratio: float = 0.9
    max_pages_without_text_ratio: float = 0.5


@dataclass
class QualityEvaluation:
    """Quality decision with triggered rule codes."""

    decision: QualityDecision
    triggered_rules: list[str] = field(default_factory=list)
    warnings: list[ParseWarning] = field(default_factory=list)


class DeterministicParseQualityGate:
    """Evaluate CanonicalDocument metrics against typed thresholds."""

    def __init__(self, thresholds: QualityThresholds | None = None) -> None:
        self.thresholds = thresholds or QualityThresholds()

    def evaluate_canonical(
        self,
        document: CanonicalDocument,
        *,
        profile: str,
    ) -> QualityEvaluation:
        """Return ACCEPT / FALLBACK_REQUIRED / HUMAN_REVIEW_REQUIRED / REJECT."""
        triggered: list[str] = []
        warnings: list[ParseWarning] = []
        metrics = document.metrics
        status = document.status

        if status is ParseStatus.ENCRYPTED:
            triggered.append("status_encrypted")
            return QualityEvaluation(QualityDecision.REJECT, triggered, warnings)
        if status is ParseStatus.UNSUPPORTED:
            triggered.append("status_unsupported")
            return QualityEvaluation(QualityDecision.REJECT, triggered, warnings)
        if status is ParseStatus.FAILED:
            triggered.append("status_failed")
            if profile == "full":
                return QualityEvaluation(QualityDecision.FALLBACK_REQUIRED, triggered, warnings)
            return QualityEvaluation(QualityDecision.HUMAN_REVIEW_REQUIRED, triggered, warnings)

        if metrics.total_character_count < self.thresholds.min_total_characters:
            triggered.append("min_total_characters")
        if metrics.empty_page_ratio > self.thresholds.max_empty_page_ratio:
            triggered.append("max_empty_page_ratio")
        if metrics.replacement_character_ratio > self.thresholds.max_replacement_character_ratio:
            triggered.append("max_replacement_character_ratio")
        if (
            metrics.total_character_count > 0
            and metrics.alphanumeric_character_ratio
            < self.thresholds.min_alphanumeric_character_ratio
        ):
            triggered.append("min_alphanumeric_character_ratio")
        if metrics.control_character_count > self.thresholds.max_control_character_count:
            triggered.append("max_control_character_count")
        if metrics.duplicate_line_ratio > self.thresholds.max_duplicate_line_ratio:
            triggered.append("max_duplicate_line_ratio")
        if metrics.page_count > 0:
            without_ratio = metrics.pages_without_text / metrics.page_count
            if without_ratio > self.thresholds.max_pages_without_text_ratio:
                triggered.append("max_pages_without_text_ratio")

        for code in triggered:
            warnings.append(
                ParseWarning(
                    code=ParseWarningCode.QUALITY_SIGNAL,
                    message=f"quality rule triggered: {code}",
                )
            )

        if not triggered and status in {ParseStatus.SUCCESS, ParseStatus.PARTIAL}:
            # PARTIAL with no quality-threshold failures can still be accepted.
            if status is ParseStatus.PARTIAL:
                # Prefer fallback in FULL for partial parses when any limit warnings exist.
                limit_warnings = [
                    w for w in document.warnings if w.code is ParseWarningCode.LIMIT_EXCEEDED
                ]
                if limit_warnings and profile == "full":
                    triggered.append("partial_with_limits")
                    return QualityEvaluation(
                        QualityDecision.FALLBACK_REQUIRED,
                        triggered,
                        warnings,
                    )
            return QualityEvaluation(QualityDecision.ACCEPT, triggered, warnings)

        # Empty / low-quality text PDFs need Docling fallback in FULL.
        empty_like = {
            "min_total_characters",
            "max_empty_page_ratio",
            "max_pages_without_text_ratio",
        }
        if (
            profile == "full"
            and triggered
            and set(triggered)
            <= empty_like
            | {
                "max_replacement_character_ratio",
                "min_alphanumeric_character_ratio",
                "max_control_character_count",
                "max_duplicate_line_ratio",
                "status_failed",
            }
        ):
            severe = {
                "max_replacement_character_ratio",
                "min_alphanumeric_character_ratio",
            } & set(triggered)
            if severe and metrics.replacement_character_ratio > 0.5:
                return QualityEvaluation(QualityDecision.REJECT, triggered, warnings)
            return QualityEvaluation(QualityDecision.FALLBACK_REQUIRED, triggered, warnings)

        if "max_replacement_character_ratio" in triggered and (
            metrics.replacement_character_ratio
            > self.thresholds.max_replacement_character_ratio * 2
        ):
            return QualityEvaluation(QualityDecision.REJECT, triggered, warnings)

        if profile == "full":
            return QualityEvaluation(QualityDecision.FALLBACK_REQUIRED, triggered, warnings)
        return QualityEvaluation(QualityDecision.HUMAN_REVIEW_REQUIRED, triggered, warnings)

    def evaluate(
        self,
        document: CanonicalDocument,
        *,
        profile: str,
    ) -> ParseQualityReport:
        """ParseQualityGate Protocol over canonical documents."""
        evaluation = self.evaluate_canonical(document, profile=profile)
        accepted = evaluation.decision is QualityDecision.ACCEPT
        return ParseQualityReport(
            decision=evaluation.decision,
            accepted=accepted,
            score=1.0 if accepted else 0.0,
            triggered_rules=list(evaluation.triggered_rules),
            reasons=list(evaluation.triggered_rules),
        )
