"""Domain-layer typed errors (no adapter dependencies)."""

from __future__ import annotations


class EvidenceAlignmentError(ValueError):
    """Raised when an evidence span cannot be aligned exactly to raw text."""

    def __init__(self, message: str = "evidence alignment failed") -> None:
        super().__init__(message)
        self.code = "EVIDENCE_ALIGNMENT"
        self.message = message
