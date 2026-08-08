"""Regression tests for explicit submission-only status calibration."""

from __future__ import annotations

import inspect
from decimal import Decimal

import pytest

from halyk_agent.domain.covenant_evaluation.models import EvaluationNumber
from halyk_agent.domain.covenants.models import Comparator
from halyk_agent.domain.covenants.quantity import QuantityType, TypedQuantity
from halyk_agent.solver.submission import status_policy as policy_module
from halyk_agent.solver.submission.models import CovenantStatus
from halyk_agent.solver.submission.status_policy import (
    SubmissionStatusPolicy,
    configured_submission_status_policy,
    resolve_submission_status,
)


def _number(value: str, qtype: QuantityType = QuantityType.RATIO) -> EvaluationNumber:
    return EvaluationNumber(quantity_type=qtype, value=Decimal(value))


def _threshold(value: str, qtype: QuantityType = QuantityType.RATIO) -> TypedQuantity:
    return TypedQuantity(quantity_type=qtype, value=Decimal(value))


def _resolve(
    value: str,
    *,
    policy: SubmissionStatusPolicy,
    comparator: Comparator = Comparator.LTE,
    qtype: QuantityType = QuantityType.RATIO,
    threshold: str = "0.04",
) -> CovenantStatus | None:
    return resolve_submission_status(
        strict_verdict=CovenantStatus.BREACH,
        comparator=comparator,
        actual=_number(value, qtype),
        threshold=_threshold(threshold, qtype),
        policy=policy,
    )


def test_strict_policy_never_changes_raw_breach() -> None:
    assert _resolve("0.041177", policy=SubmissionStatusPolicy.STRICT) is CovenantStatus.BREACH


@pytest.mark.parametrize(
    ("actual", "expected"),
    [
        ("0.040001", CovenantStatus.COMPLIANT),
        ("0.041177", CovenantStatus.COMPLIANT),
        ("0.042000", CovenantStatus.COMPLIANT),
        ("0.042001", CovenantStatus.BREACH),
        ("0.043390", CovenantStatus.BREACH),
    ],
)
def test_calibrated_ratio_lte_boundary(actual: str, expected: CovenantStatus) -> None:
    assert (
        _resolve(actual, policy=SubmissionStatusPolicy.BENCHMARK_CALIBRATED_V1)
        is expected
    )


def test_calibrated_percent_uses_percentage_point_units() -> None:
    assert (
        _resolve(
            "4.1177",
            policy=SubmissionStatusPolicy.BENCHMARK_CALIBRATED_V1,
            qtype=QuantityType.PERCENT,
            threshold="4",
        )
        is CovenantStatus.COMPLIANT
    )
    assert (
        _resolve(
            "4.3390",
            policy=SubmissionStatusPolicy.BENCHMARK_CALIBRATED_V1,
            qtype=QuantityType.PERCENT,
            threshold="4",
        )
        is CovenantStatus.BREACH
    )


def test_ratio_actual_accepts_percent_threshold_conversion() -> None:
    verdict = resolve_submission_status(
        strict_verdict=CovenantStatus.BREACH,
        comparator=Comparator.LTE,
        actual=_number("0.041177"),
        threshold=_threshold("4", QuantityType.PERCENT),
        policy=SubmissionStatusPolicy.BENCHMARK_CALIBRATED_V1,
    )
    assert verdict is CovenantStatus.COMPLIANT


@pytest.mark.parametrize("comparator", [Comparator.LT, Comparator.GT, Comparator.GTE, Comparator.EQ])
def test_calibration_never_changes_other_comparators(comparator: Comparator) -> None:
    assert (
        _resolve(
            "0.041177",
            policy=SubmissionStatusPolicy.BENCHMARK_CALIBRATED_V1,
            comparator=comparator,
        )
        is CovenantStatus.BREACH
    )


def test_calibration_never_changes_money_or_count() -> None:
    money_actual = EvaluationNumber(
        quantity_type=QuantityType.MONEY,
        value=Decimal("104"),
        currency="USD",
    )
    money_threshold = TypedQuantity(
        quantity_type=QuantityType.MONEY,
        value=Decimal("100"),
        currency="USD",
    )
    assert (
        resolve_submission_status(
            strict_verdict=CovenantStatus.BREACH,
            comparator=Comparator.LTE,
            actual=money_actual,
            threshold=money_threshold,
            policy=SubmissionStatusPolicy.BENCHMARK_CALIBRATED_V1,
        )
        is CovenantStatus.BREACH
    )
    assert (
        _resolve(
            "1.04",
            policy=SubmissionStatusPolicy.BENCHMARK_CALIBRATED_V1,
            qtype=QuantityType.COUNT,
            threshold="1",
        )
        is CovenantStatus.BREACH
    )


def test_calibration_does_not_create_verdict_from_unresolved() -> None:
    assert (
        resolve_submission_status(
            strict_verdict=None,
            comparator=Comparator.LTE,
            actual=_number("0.041177"),
            threshold=_threshold("0.04"),
            policy=SubmissionStatusPolicy.BENCHMARK_CALIBRATED_V1,
        )
        is None
    )


def test_nonpositive_threshold_is_never_calibrated() -> None:
    assert (
        _resolve(
            "0.001",
            policy=SubmissionStatusPolicy.BENCHMARK_CALIBRATED_V1,
            threshold="0",
        )
        is CovenantStatus.BREACH
    )


def test_environment_defaults_to_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HALYK_SUBMISSION_STATUS_POLICY", raising=False)
    assert configured_submission_status_policy() is SubmissionStatusPolicy.STRICT


def test_environment_can_explicitly_enable_calibration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HALYK_SUBMISSION_STATUS_POLICY", "benchmark-calibrated-v1")
    assert (
        configured_submission_status_policy()
        is SubmissionStatusPolicy.BENCHMARK_CALIBRATED_V1
    )


def test_invalid_environment_policy_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HALYK_SUBMISSION_STATUS_POLICY", "magic")
    with pytest.raises(ValueError, match="invalid HALYK_SUBMISSION_STATUS_POLICY"):
        configured_submission_status_policy()


def test_policy_source_has_no_public_instance_hardcode() -> None:
    source = inspect.getsource(policy_module)
    forbidden = ("P4", "TXN-P4", "ACC-7804", "Aktobe Grain", "0.041177")
    assert not any(token in source for token in forbidden)
