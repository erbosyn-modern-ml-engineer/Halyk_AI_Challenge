"""Generic page text/OCR quality signals (filename-agnostic)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from halyk_agent.domain.parsing import CanonicalPage, ParseStatus

# Cyrillic capitals are intentional for KK/RU heading detection.
_HEADING_RE = re.compile(
    r"^(#{1,6}\s+|[A-Z\u0410-\u042F\u0401\u04A2\u0492\u04AE\u04B0\u04BA\u0406"
    r"0-9 .,]{3,80})$"
)


class PageQualityState(StrEnum):
    TEXT_OK = "TEXT_OK"
    LOW_TEXT = "LOW_TEXT"
    IMAGE_DOMINANT = "IMAGE_DOMINANT"
    HEADING_WITHOUT_BODY = "HEADING_WITHOUT_BODY"
    OCR_REQUIRED = "OCR_REQUIRED"
    OCR_SUCCEEDED = "OCR_SUCCEEDED"
    OCR_FAILED = "OCR_FAILED"
    UNREADABLE = "UNREADABLE"


@dataclass(frozen=True)
class PageSignals:
    char_count: int
    alphanumeric_ratio: float
    replacement_ratio: float
    image_count: int
    heading_without_body: bool
    empty_table_near_heading: bool
    parser_status: str | None


def _ratios(text: str) -> tuple[int, float, float]:
    chars = len(text)
    if chars == 0:
        return 0, 0.0, 0.0
    alnum = sum(1 for ch in text if ch.isalnum())
    repl = text.count("\ufffd")
    return chars, alnum / chars, repl / chars


def _heading_without_body(text: str) -> bool:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return False
    if len(lines) > 3:
        return False
    heading_like = sum(1 for ln in lines if _HEADING_RE.match(ln) or len(ln) <= 40)
    body_chars = sum(len(ln) for ln in lines)
    return heading_like >= 1 and body_chars < 80


def classify_signals(signals: PageSignals) -> PageQualityState:
    if signals.char_count == 0 and signals.image_count >= 1:
        return PageQualityState.OCR_REQUIRED
    if signals.heading_without_body and signals.char_count < 120:
        return PageQualityState.OCR_REQUIRED
    if signals.image_count >= 1 and signals.char_count < 40:
        return PageQualityState.IMAGE_DOMINANT
    if signals.heading_without_body:
        return PageQualityState.HEADING_WITHOUT_BODY
    if signals.char_count < 25 or signals.alphanumeric_ratio < 0.15:
        return PageQualityState.LOW_TEXT
    if signals.replacement_ratio > 0.2:
        return PageQualityState.UNREADABLE
    return PageQualityState.TEXT_OK


def diagnose_canonical_page(
    page: CanonicalPage,
    *,
    image_count: int = 0,
    parser_status: ParseStatus | None = None,
) -> tuple[PageQualityState, PageSignals]:
    chars, alnum, repl = _ratios(page.raw_text)
    heading = _heading_without_body(page.raw_text)
    empty_table = bool(page.tables) and chars < 40
    signals = PageSignals(
        char_count=chars,
        alphanumeric_ratio=alnum,
        replacement_ratio=repl,
        image_count=image_count,
        heading_without_body=heading,
        empty_table_near_heading=empty_table and heading,
        parser_status=parser_status.value if parser_status else None,
    )
    return classify_signals(signals), signals


def trusted_success_blocked(status: ParseStatus, page_states: list[PageQualityState]) -> bool:
    """SUCCESS is not trusted when any load-bearing page still needs OCR."""
    if status is not ParseStatus.SUCCESS:
        return False
    return any(state is PageQualityState.OCR_REQUIRED for state in page_states)


def page_image_count_from_pypdf(page: object) -> int:
    """Best-effort image XObject count for a pypdf page object."""
    count = 0
    try:
        resources = page.get("/Resources")  # type: ignore[attr-defined]
        if resources is None:
            return 0
        resolved = resources.get_object() if hasattr(resources, "get_object") else resources
        xobject = resolved.get("/XObject") if resolved else None
        if xobject is None:
            return 0
        xobj = xobject.get_object() if hasattr(xobject, "get_object") else xobject
        for key in xobj:
            item = xobj[key]
            item_obj = item.get_object() if hasattr(item, "get_object") else item
            if str(item_obj.get("/Subtype", "")) == "/Image":
                count += 1
    except Exception:
        return count
    return count
