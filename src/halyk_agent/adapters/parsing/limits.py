"""Parser limit defaults and helpers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ParserLimits:
    """Hard limits for document parsing."""

    max_pdf_pages: int = 500
    max_page_characters: int = 500_000
    max_document_characters: int = 5_000_000
    max_parser_warnings: int = 200
