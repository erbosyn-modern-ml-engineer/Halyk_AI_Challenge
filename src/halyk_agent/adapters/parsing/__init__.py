"""Parsing adapter package."""

from __future__ import annotations

from halyk_agent.adapters.parsing.errors import (
    DocumentParsingError,
    EncryptedDocumentError,
    EvidenceAlignmentError,
    ParseCacheError,
    ParserDependencyMissingError,
    ParserLimitExceededError,
    ParserOutputInvalidError,
    UnsupportedDocumentFormatError,
)
from halyk_agent.adapters.parsing.pypdf_parser import PyPdfDocumentParser

__all__ = [
    "DocumentParsingError",
    "EncryptedDocumentError",
    "EvidenceAlignmentError",
    "ParseCacheError",
    "ParserDependencyMissingError",
    "ParserLimitExceededError",
    "ParserOutputInvalidError",
    "PyPdfDocumentParser",
    "UnsupportedDocumentFormatError",
]
