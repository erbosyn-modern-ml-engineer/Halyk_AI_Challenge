"""Typed document-parsing errors (no secret/path leakage)."""

from __future__ import annotations

from halyk_agent.domain.errors import EvidenceAlignmentError


class DocumentParsingError(Exception):
    """Base class for document parsing failures."""

    def __init__(self, message: str, *, code: str = "PARSER_ERROR") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class UnsupportedDocumentFormatError(DocumentParsingError):
    """Raised when the format is not supported by the selected parser."""

    def __init__(self, message: str = "unsupported document format") -> None:
        super().__init__(message, code="UNSUPPORTED_FORMAT")


class EncryptedDocumentError(DocumentParsingError):
    """Raised when a document is encrypted and no password is available."""

    def __init__(self, message: str = "encrypted document") -> None:
        super().__init__(message, code="ENCRYPTED")


class ParserLimitExceededError(DocumentParsingError):
    """Raised when a configured parse limit is exceeded."""

    def __init__(self, message: str = "parser limit exceeded") -> None:
        super().__init__(message, code="LIMIT_EXCEEDED")


class ParserDependencyMissingError(DocumentParsingError):
    """Raised when an optional parser dependency is not installed."""

    def __init__(self, message: str = "parser dependency missing") -> None:
        super().__init__(message, code="DEPENDENCY_MISSING")


class ParserOutputInvalidError(DocumentParsingError):
    """Raised when parser output cannot be mapped to canonical models."""

    def __init__(self, message: str = "parser output invalid") -> None:
        super().__init__(message, code="PARSER_OUTPUT_INVALID")


class ParseCacheError(DocumentParsingError):
    """Raised for unrecoverable cache infrastructure errors."""

    def __init__(self, message: str = "parse cache error") -> None:
        super().__init__(message, code="CACHE_ERROR")


__all__ = [
    "DocumentParsingError",
    "EncryptedDocumentError",
    "EvidenceAlignmentError",
    "ParseCacheError",
    "ParserDependencyMissingError",
    "ParserLimitExceededError",
    "ParserOutputInvalidError",
    "UnsupportedDocumentFormatError",
]
