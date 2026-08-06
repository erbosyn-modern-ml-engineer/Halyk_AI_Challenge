"""Typed solver errors."""

from __future__ import annotations


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


class LeakageAttemptError(SolverError):
    pass
