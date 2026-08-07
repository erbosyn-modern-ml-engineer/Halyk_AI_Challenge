"""Bounded evidence windows for deterministic and LLM fact extraction."""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from halyk_agent.domain.common import NonEmptyStr
from halyk_agent.domain.fact_extraction.models import FactRequirement
from halyk_agent.domain.fact_extraction.text_normalize import cue_corpus
from halyk_agent.domain.ids import deterministic_id, sha256_text
from halyk_agent.domain.parsing import CanonicalDocument

_MAX_WINDOW_CHARS = 3500
_NEIGHBOR_PARAS = 2


class EvidenceFragment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fragment_id: NonEmptyStr
    page_number: int = Field(ge=1)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    text: NonEmptyStr
    heading: str | None = None


class EvidenceWindow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    window_id: NonEmptyStr
    requirement_id: NonEmptyStr
    document_id: NonEmptyStr
    source_sha256: NonEmptyStr
    fragments: tuple[EvidenceFragment, ...]
    window_hash: NonEmptyStr


@dataclass(frozen=True, slots=True)
class _Paragraph:
    page_number: int
    char_start: int
    char_end: int
    text: str
    heading: str | None


def _split_paragraphs(page_number: int, text: str) -> list[_Paragraph]:
    paras: list[_Paragraph] = []
    offset = 0
    last_heading: str | None = None
    for part in re.split(r"\n\s*\n|\n(?=[A-Z\u0410-\u042f\u0401(])", text):
        if not part.strip():
            offset += len(part)
            # account for splitter consumption is approximate; use find from offset
            continue
        idx = text.find(part, offset)
        if idx < 0:
            idx = offset
        start = idx
        end = idx + len(part)
        stripped = part.strip()
        is_heading = len(stripped) < 120 and (
            stripped.isupper()
            or stripped.startswith("Примечание")
            or stripped.startswith("Note ")
            or "бенефициар" in stripped.casefold()
            or "ownership" in stripped.casefold()
        )
        heading = stripped if is_heading else last_heading
        if is_heading:
            last_heading = stripped
        paras.append(
            _Paragraph(
                page_number=page_number,
                char_start=start,
                char_end=end,
                text=part,
                heading=heading,
            )
        )
        offset = end
    if not paras and text.strip():
        paras.append(
            _Paragraph(
                page_number=page_number,
                char_start=0,
                char_end=len(text),
                text=text,
                heading=None,
            )
        )
    return paras


def _cue_hit(text: str, cues: tuple[str, ...]) -> bool:
    lowered = cue_corpus(text).casefold()
    if any(cue.casefold() in lowered for cue in cues if cue):
        return True
    # Ownership/threshold tables may keep ASCII entity+% rows while cue words are mojibaked.
    ownershipish = any(
        cue.casefold()
        in {"владе", "ownership", "голосующ", "бенефициар", "%", "связанн", "related"}
        for cue in cues
    )
    return bool(
        ownershipish
        and re.search(
            r"(?:LLP|JSC|Inc\.?|Bureau|Partners|Capital|Logistics)\s+\d+(?:[.,]\d+)?\s*%",
            text,
        )
    )


def _answer_density(text: str, cues: tuple[str, ...]) -> int:
    """Prefer paragraphs that themselves look answer-capable (tables, %, TXN)."""
    score = 0
    lowered = cue_corpus(text).casefold()
    if any(cue.casefold() in lowered for cue in cues if cue):
        score += 2
    score += len(re.findall(r"\d+(?:[.,]\d+)?\s*%", text))
    score += 3 * len(re.findall(r"\b(?:LLP|JSC)\b", text))
    score += 2 * len(re.findall(r"TXN-[A-Za-z0-9]+-\d+", text))
    return score


def select_windows(
    requirement: FactRequirement,
    document: CanonicalDocument,
    *,
    max_fragments: int = 12,
) -> EvidenceWindow | None:
    """
    Build a bounded evidence window around paragraphs matching lexical cues.

    Fragment ids are local to the window: F001, F002, …
    Prefer high answer-density hits (ownership tables) over weak later-page cues.
    """
    cues = requirement.strong_lexical_cues or requirement.lexical_cues
    if not cues:
        cues = requirement.lexical_cues
    if not cues:
        return None

    all_paras: list[_Paragraph] = []
    for page in sorted(document.pages, key=lambda item: item.page_number):
        all_paras.extend(_split_paragraphs(page.page_number, page.raw_text or ""))

    hit_indexes = [i for i, para in enumerate(all_paras) if _cue_hit(para.text, cues)]
    if not hit_indexes:
        return None

    # Rank hits so table pages beat boilerplate "структура владения" pages.
    hit_indexes.sort(key=lambda i: (-_answer_density(all_paras[i].text, cues), i))
    primary = hit_indexes[0]
    selected: dict[int, _Paragraph] = {}
    lo = max(0, primary - _NEIGHBOR_PARAS)
    hi = min(len(all_paras), primary + _NEIGHBOR_PARAS + 1)
    for j in range(lo, hi):
        selected[j] = all_paras[j]
    # Include other strong hits on the same page as the primary hit.
    primary_page = all_paras[primary].page_number
    for idx in hit_indexes[1:]:
        if all_paras[idx].page_number != primary_page:
            continue
        if _answer_density(all_paras[idx].text, cues) <= 0:
            continue
        selected[idx] = all_paras[idx]

    ordered = [selected[i] for i in sorted(selected)]
    fragments: list[EvidenceFragment] = []
    total = 0
    for ordinal, para in enumerate(ordered, start=1):
        if len(fragments) >= max_fragments:
            break
        text = para.text.strip()
        if not text:
            continue
        if total + len(text) > _MAX_WINDOW_CHARS and fragments:
            break
        frag_id = f"F{ordinal:03d}"
        fragments.append(
            EvidenceFragment(
                fragment_id=frag_id,
                page_number=para.page_number,
                char_start=para.char_start,
                char_end=para.char_end,
                text=text,
                heading=para.heading,
            )
        )
        total += len(text)

    if not fragments:
        return None

    payload = "|".join(
        f"{f.fragment_id}:{f.page_number}:{f.char_start}:{f.char_end}:{f.text}" for f in fragments
    )
    window_hash = sha256_text(payload)
    return EvidenceWindow(
        window_id=deterministic_id(
            "evidence-window",
            requirement.requirement_id,
            document.document_id,
            window_hash,
        ),
        requirement_id=requirement.requirement_id,
        document_id=document.document_id,
        source_sha256=document.source_sha256,
        fragments=tuple(fragments),
        window_hash=window_hash,
    )


def fragment_ids_in_window(window: EvidenceWindow) -> frozenset[str]:
    return frozenset(f.fragment_id for f in window.fragments)
