"""Typed solver errors."""

from __future__ import annotations

from halyk_agent.dataset_access import LeakageAttemptError as DatasetLeakageAttemptError


class SolverError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class DatasetAdapterError(SolverError):
    pass


class AnswerKeyAccessBlockedError(SolverError):
    pass


class SubmissionSchemaError(SolverError):
    pass


class LeakageAttemptError(DatasetLeakageAttemptError, SolverError):
    """Answer-key / GT leakage attempt (also a SolverError)."""

    def __init__(self, message: str) -> None:
        DatasetLeakageAttemptError.__init__(self, message)
        SolverError.__init__(self, message)
