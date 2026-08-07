"""Concurrency-safe external HTTP attempt budget (claim-before-request)."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


class BudgetExhaustedError(RuntimeError):
    """Raised when no external HTTP permit remains."""


@dataclass
class ExternalAttemptBudget:
    """
    Hard cap on real HTTP attempts.

    Provider contract: any runtime provider that performs internal retries MUST
    call ``claim()`` / ``try_claim()`` immediately BEFORE each actual HTTP
    request (including empty-content retries). Escalations also claim one permit
    each. Never infer cost after the fact from call deltas.
    """

    max_attempts: int
    _used: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def claim(self) -> None:
        """Reserve one HTTP permit or raise BudgetExhaustedError."""
        with self._lock:
            if self._used >= self.max_attempts:
                raise BudgetExhaustedError("MAX_EXTERNAL_ATTEMPTS")
            self._used += 1

    def try_claim(self) -> bool:
        """Reserve one HTTP permit; return False when exhausted."""
        with self._lock:
            if self._used >= self.max_attempts:
                return False
            self._used += 1
            return True

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    @property
    def remaining(self) -> int:
        with self._lock:
            return max(0, self.max_attempts - self._used)
