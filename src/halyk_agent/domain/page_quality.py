"""Generic page text/OCR quality signals (filename-agnostic)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

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


class ImageVisibility(StrEnum):
    """Whether image_count is a verified observation."""

    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"


class PageVisualSignals(BaseModel):
    """Per-page visual metadata for trust gating (no image bytes)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    page_number: int = Field(ge=1)
    image_count: int = Field(ge=0, default=0)
    image_visibility: ImageVisibility = ImageVisibility.UNKNOWN
    displayed_image_count: int | None = Field(default=None, ge=0)
    extraction_warnings: list[str] = Field(default_factory=list)


BLOCKING_PAGE_QUALITY_STATES: frozenset[PageQualityState] = frozenset(
    {
        PageQualityState.OCR_REQUIRED,
        PageQualityState.IMAGE_DOMINANT,
        PageQualityState.HEADING_WITHOUT_BODY,
        PageQualityState.UNREADABLE,
    }
)


def is_blocking_page_quality(state: PageQualityState) -> bool:
    """Canonical predicate used by gate, cache validation, and tests."""
    return state in BLOCKING_PAGE_QUALITY_STATES


@dataclass(frozen=True)
class PageSignals:
    char_count: int
    alphanumeric_ratio: float
    replacement_ratio: float
    image_count: int
    image_visibility: ImageVisibility
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
    """Classify page quality from text + visual metadata."""
    known_images = signals.image_visibility is ImageVisibility.KNOWN and signals.image_count >= 1
    unknown_visuals = signals.image_visibility is ImageVisibility.UNKNOWN

    # Verified images with empty / insufficient text → blocking.
    if known_images and signals.char_count == 0:
        return PageQualityState.OCR_REQUIRED
    if signals.heading_without_body and signals.char_count < 120:
        return PageQualityState.OCR_REQUIRED
    if known_images and signals.char_count < 40:
        return PageQualityState.IMAGE_DOMINANT

    # Conservative: UNKNOWN visuals + no usable text must not be trusted SUCCESS.
    if unknown_visuals and signals.char_count == 0:
        return PageQualityState.OCR_REQUIRED
    if unknown_visuals and signals.char_count < 25 and signals.alphanumeric_ratio < 0.15:
        return PageQualityState.OCR_REQUIRED

    # Verified zero images + completely empty page is not a usable text page.
    if (
        signals.image_visibility is ImageVisibility.KNOWN
        and signals.image_count == 0
        and signals.char_count == 0
    ):
        return PageQualityState.UNREADABLE

    if signals.heading_without_body:
        return PageQualityState.HEADING_WITHOUT_BODY
    if signals.char_count < 25 or signals.alphanumeric_ratio < 0.15:
        # LOW_TEXT is non-blocking only when visuals are KNOWN with zero images
        # and some short text remains (verified text-only short page).
        return PageQualityState.LOW_TEXT
    if signals.replacement_ratio > 0.2:
        return PageQualityState.UNREADABLE
    return PageQualityState.TEXT_OK


def diagnose_canonical_page(
    page: CanonicalPage,
    *,
    visual: PageVisualSignals | None = None,
    image_count: int | None = None,
    image_visibility: ImageVisibility | None = None,
    parser_status: ParseStatus | None = None,
) -> tuple[PageQualityState, PageSignals]:
    chars, alnum, repl = _ratios(page.raw_text)
    heading = _heading_without_body(page.raw_text)
    empty_table = bool(page.tables) and chars < 40
    if visual is not None:
        vis = visual.image_visibility
        img = visual.image_count
    else:
        vis = image_visibility or ImageVisibility.KNOWN
        img = int(image_count or 0)
    signals = PageSignals(
        char_count=chars,
        alphanumeric_ratio=alnum,
        replacement_ratio=repl,
        image_count=img,
        image_visibility=vis,
        heading_without_body=heading,
        empty_table_near_heading=empty_table and heading,
        parser_status=parser_status.value if parser_status else None,
    )
    return classify_signals(signals), signals


def page_image_count_from_pypdf(page: object) -> tuple[int, ImageVisibility, list[str]]:
    """Count displayed/inline images on a pypdf page without writing bytes.

    Returns (count, visibility, warnings). UNKNOWN when inspection fails entirely.
    """
    warnings: list[str] = []
    counts: list[int] = []

    # Prefer page.images when available (pypdf >= 3.x).
    # Use len() only — materialising the lazy sequence constructs ImageFile objects
    # and may decode embedded images merely to count them.
    try:
        images = getattr(page, "images", None)
        if images is not None:
            counts.append(len(images))
    except Exception as exc:
        warnings.append(f"page.images_failed:{exc.__class__.__name__}")

    # XObject /Image subtypes (inline + referenced).
    try:
        resources = page.get("/Resources")  # type: ignore[attr-defined]
        if resources is not None:
            resolved = resources.get_object() if hasattr(resources, "get_object") else resources
            xobject = resolved.get("/XObject") if resolved else None
            if xobject is not None:
                xobj = xobject.get_object() if hasattr(xobject, "get_object") else xobject
                count = 0
                for key in xobj:
                    item = xobj[key]
                    item_obj = item.get_object() if hasattr(item, "get_object") else item
                    if str(item_obj.get("/Subtype", "")) == "/Image":
                        count += 1
                counts.append(count)
    except Exception as exc:
        warnings.append(f"xobject_images_failed:{exc.__class__.__name__}")

    if not counts:
        return 0, ImageVisibility.UNKNOWN, warnings or ["image_inspection_unavailable"]
    # Deterministic: take max of available methods (covers both inline and referenced).
    return max(counts), ImageVisibility.KNOWN, warnings
