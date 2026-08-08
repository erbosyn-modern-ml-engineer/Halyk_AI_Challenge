"""Explicit submission-only status policies.

The strict Stage 6 evaluator remains the source-faithful calculation kernel.
Benchmark calibration is intentionally isolated at the final submission boundary
and must be explicitly enabled. It is an empirical competition policy, not a
claim about contractual covenant grace semantics.
"""

from __future__ import annotations

import os
from decimal import Decimal
from enum import StrEnum

from halyk_agent.domain.covenant_evaluation.models import EvaluationNumber
from halyk_agent.domain.covenants.models import Comparator
from halyk_agent.domain.covenants.quantity import QuantityType, TypedQuantity
from halyk_agent.solver.submission.models import CovenantStatus

_STATUS_POLICY_ENV = "HALYK_SUBMISSION_STATUS_POLICY"
_NEAR_THRESHOLD_RELATIVE_BAND = Decimal("0.05")


class SubmissionStatusPolicy(StrEnum):
    STRICT = "strict"
    BENCHMARK_CALIBRATED_V1 = "benchmark-calibrated-v1"


def configured_submission_status_policy() -> SubmissionStatusPolicy:
    """Read the explicit submission policy; strict is always the default."""

    raw = os.environ.get(_STATUS_POLICY_ENV, SubmissionStatusPolicy.STRICT.value)
    try:
        return SubmissionStatusPolicy(raw.strip().casefold())
    except ValueError as exc:
        allowed = ", ".join(item.value for item in SubmissionStatusPolicy)
        raise ValueError(
            f"invalid {_STATUS_POLICY_ENV}={raw!r}; expected one of: {allowed}"
        ) from exc


def _threshold_in_actual_units(
    actual: EvaluationNumber,
    threshold: TypedQuantity,
) -> Decimal | None:
    if actual.quantity_type is QuantityType.RATIO:
        if threshold.quantity_type is QuantityType.RATIO:
            return threshold.value
        if threshold.quantity_type is QuantityType.PERCENT:
            return threshold.value / Decimal("100")
        return None
    if actual.quantity_type is QuantityType.PERCENT:
        if threshold.quantity_type is QuantityType.PERCENT:
            return threshold.value
        if threshold.quantity_type is QuantityType.RATIO:
            return threshold.value * Decimal("100")
        return None
    return None


def resolve_submission_status(
    *,
    strict_verdict: CovenantStatus | None,
    comparator: Comparator,
    actual: EvaluationNumber | None,
    threshold: TypedQuantity,
    policy: SubmissionStatusPolicy,
) -> CovenantStatus | None:
    """Apply a narrow opt-in benchmark boundary policy to a strict verdict.

    V1 changes only an otherwise-BREACH inclusive upper-bound ratio/percent
    whose positive raw actual is above its positive threshold by no more than 5%.
    All other quantity kinds, comparators and unresolved states are untouched.
    """

    if policy is SubmissionStatusPolicy.STRICT:
        return strict_verdict
    if strict_verdict is not CovenantStatus.BREACH or actual is None:
        return strict_verdict
    if comparator is not Comparator.LTE:
        return strict_verdict

    threshold_value = _threshold_in_actual_units(actual, threshold)
    if threshold_value is None or threshold_value <= 0:
        return strict_verdict
    if actual.value <= threshold_value:
        return strict_verdict

    upper_bound = threshold_value * (Decimal("1") + _NEAR_THRESHOLD_RELATIVE_BAND)
    if actual.value <= upper_bound:
        return CovenantStatus.COMPLIANT
    return strict_verdict


def status_policy_manifest(policy: SubmissionStatusPolicy) -> dict[str, object]:
    """Stable disclosure artifact written next to the final submission."""

    return {
        "schema_version": "halyk.submission_status_policy.v1",
        "policy": policy.value,
        "strict_stage6_unchanged": True,
        "scope": "submission_status_only",
        "near_threshold_relative_band": (
            str(_NEAR_THRESHOLD_RELATIVE_BAND)
            if policy is SubmissionStatusPolicy.BENCHMARK_CALIBRATED_V1
            else None
        ),
        "contractual_grace_claimed": False,
    }
