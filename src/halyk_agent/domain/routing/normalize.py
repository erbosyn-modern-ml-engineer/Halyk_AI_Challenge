"""Conservative deterministic legal-name normalization (Stage 5B.1).

identity_key  — ACCEPT match (preserves legal-form identity)
base_key      — candidate generation / LEGAL_FORM_MISMATCH diagnostics only
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from halyk_agent.domain.routing.models import AliasKind, CompanyAlias

# Explicit legal-form tokens (casefolded) → canonical form class.
# Language variants of the same form may share a class; distinct forms never do.
_LEGAL_FORM_CANON: dict[str, str] = {
    "jsc": "jsc",
    "ao": "jsc",  # AO language variant of JSC
    "\u0430\u043e": "jsc",
    "llp": "llp",
    "llc": "llc",
    "too": "too",
    "\u0442\u043e\u043e": "too",
    "inc": "inc",
    "ltd": "ltd",
    "gmbh": "gmbh",
}

_LEGAL_SUFFIXES: frozenset[str] = frozenset(_LEGAL_FORM_CANON.keys())

_QUOTE_MAP = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "«": '"',
        "»": '"',
        "„": '"',
        "‟": '"',
    }
)

_PUNCT_SPACE_RE = re.compile(r"\s*([,.;:!?/\\|&+])\s*")
_MULTI_SPACE_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[^\s]+")
_QUOTE_CHARS = frozenset("\"'`")


@dataclass(frozen=True, slots=True)
class NormalizedLegalName:
    """Dual-key legal-name representation."""

    raw: str
    identity_key: str
    identity_tokens: tuple[str, ...]
    base_key: str
    base_tokens: tuple[str, ...]
    legal_form: str | None
    aliases: tuple[CompanyAlias, ...]


def _collapse_whitespace(value: str) -> str:
    return _MULTI_SPACE_RE.sub(" ", value).strip()


def _strip_quote_glyphs(value: str) -> str:
    step = value.strip()
    while len(step) >= 2 and step[0] in _QUOTE_CHARS and step[-1] in _QUOTE_CHARS:
        step = step[1:-1].strip()
    tokens = [tok.strip("\"'`") for tok in step.split()]
    return " ".join(tok for tok in tokens if tok)


def normalize_quotes(value: str) -> str:
    return value.translate(_QUOTE_MAP)


def normalize_punctuation_spacing(value: str) -> str:
    return _PUNCT_SPACE_RE.sub(r"\1 ", value)


def tokenize_normalized(value: str) -> tuple[str, ...]:
    return tuple(_TOKEN_RE.findall(value))


def _preprocess(raw: str) -> tuple[str, list[CompanyAlias]]:
    aliases: list[CompanyAlias] = []
    step = unicodedata.normalize("NFKC", raw).strip()
    quoted = _strip_quote_glyphs(normalize_quotes(step))
    if quoted != step:
        aliases.append(
            CompanyAlias(
                canonical_candidate=quoted,
                variant=step,
                alias_kind=AliasKind.QUOTED_NAME,
                derived=True,
            )
        )
    punct = _strip_quote_glyphs(_collapse_whitespace(normalize_punctuation_spacing(quoted)))
    if punct != quoted:
        aliases.append(
            CompanyAlias(
                canonical_candidate=punct,
                variant=quoted,
                alias_kind=AliasKind.PUNCTUATION,
                derived=True,
            )
        )
    folded = _collapse_whitespace(punct.casefold())
    return folded, aliases


def normalize_legal_name_keys(
    raw: str,
    *,
    record_aliases: bool = True,
) -> NormalizedLegalName:
    """Build identity_key (form-preserving) and base_key (form-stripped)."""
    folded, aliases = _preprocess(raw)
    tokens = tokenize_normalized(folded)
    legal_form: str | None = None
    base_tokens = tokens
    identity_tokens = tokens
    if tokens and tokens[-1] in _LEGAL_FORM_CANON:
        legal_form = _LEGAL_FORM_CANON[tokens[-1]]
        base_tokens = tokens[:-1]
        identity_tokens = (*base_tokens, legal_form)
        if record_aliases and tokens[-1] != legal_form:
            aliases.append(
                CompanyAlias(
                    canonical_candidate=" ".join(identity_tokens),
                    variant=" ".join(tokens),
                    alias_kind=AliasKind.LEGAL_SUFFIX,
                    derived=True,
                )
            )
    identity_key = " ".join(identity_tokens)
    base_key = " ".join(base_tokens)
    return NormalizedLegalName(
        raw=raw,
        identity_key=identity_key,
        identity_tokens=identity_tokens,
        base_key=base_key,
        base_tokens=base_tokens,
        legal_form=legal_form,
        aliases=tuple(aliases) if record_aliases else (),
    )


def normalize_legal_name(
    raw: str,
    *,
    strip_suffixes: bool = False,
    record_aliases: bool = True,
) -> tuple[str, tuple[str, ...], tuple[CompanyAlias, ...]]:
    """
    Compatibility wrapper.

    Default (Stage 5B.1): returns identity_key (legal form preserved).
    strip_suffixes=True returns base_key only for diagnostics — never for ACCEPT.
    """
    result = normalize_legal_name_keys(raw, record_aliases=record_aliases)
    if strip_suffixes:
        return result.base_key, result.base_tokens, result.aliases
    return result.identity_key, result.identity_tokens, result.aliases


def names_match_exact(left_raw: str, right_raw: str) -> bool:
    """True only when identity_key token sequences are identical."""
    left = normalize_legal_name_keys(left_raw, record_aliases=False)
    right = normalize_legal_name_keys(right_raw, record_aliases=False)
    return bool(left.identity_tokens) and left.identity_tokens == right.identity_tokens


def legal_form_mismatch(left_raw: str, right_raw: str) -> bool:
    """True when base_key matches but legal-form identity differs."""
    left = normalize_legal_name_keys(left_raw, record_aliases=False)
    right = normalize_legal_name_keys(right_raw, record_aliases=False)
    if not left.base_tokens or left.base_tokens != right.base_tokens:
        return False
    return left.legal_form != right.legal_form


def normalize_account_id(raw: str) -> str:
    """Normalize account identifier: NFKC, trim, uppercase ASCII letters."""
    step = unicodedata.normalize("NFKC", raw).strip()
    return step.upper()
