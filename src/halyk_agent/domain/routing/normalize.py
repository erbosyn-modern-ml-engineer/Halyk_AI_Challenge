"""Conservative deterministic legal-name normalization (Stage 5B)."""

from __future__ import annotations

import re
import unicodedata

from halyk_agent.domain.routing.models import AliasKind, CompanyAlias

# Explicitly enumerated legal-form suffixes (casefolded).
_LEGAL_SUFFIXES: frozenset[str] = frozenset(
    {
        "jsc",
        "llp",
        "llc",
        "ao",
        "too",
        "\u0430\u043e",
        "\u0442\u043e\u043e",
        "inc",
        "ltd",
        "gmbh",
    }
)

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
    """Normalize spacing around common punctuation without deleting tokens."""
    return _PUNCT_SPACE_RE.sub(r"\1 ", value)


def tokenize_normalized(value: str) -> tuple[str, ...]:
    return tuple(_TOKEN_RE.findall(value))


def strip_legal_suffixes(tokens: tuple[str, ...]) -> tuple[tuple[str, ...], bool]:
    """Optionally remove trailing enumerated legal-form suffixes."""
    if not tokens:
        return tokens, False
    stripped = list(tokens)
    removed = False
    while stripped and stripped[-1].casefold() in _LEGAL_SUFFIXES:
        stripped.pop()
        removed = True
    return tuple(stripped), removed


def normalize_legal_name(
    raw: str,
    *,
    strip_suffixes: bool = True,
    record_aliases: bool = True,
) -> tuple[str, tuple[str, ...], tuple[CompanyAlias, ...]]:
    """
    Return (normalized_comparison_form, tokens, aliases).

    Allowed transforms only: NFKC, trim, casefold, whitespace collapse,
    quote normalization, punctuation spacing, optional legal-form suffix strip.
    Full token-sequence equality is required for identity matches.
    """
    aliases: list[CompanyAlias] = []
    step = unicodedata.normalize("NFKC", raw)
    step = step.strip()
    quoted = _strip_quote_glyphs(normalize_quotes(step))
    if record_aliases and quoted != step:
        aliases.append(
            CompanyAlias(
                canonical_candidate=quoted,
                variant=step,
                alias_kind=AliasKind.QUOTED_NAME,
                derived=True,
            )
        )
    punct = normalize_punctuation_spacing(quoted)
    punct = _collapse_whitespace(punct)
    punct = _strip_quote_glyphs(punct)
    if record_aliases and punct != quoted:
        aliases.append(
            CompanyAlias(
                canonical_candidate=punct,
                variant=quoted,
                alias_kind=AliasKind.PUNCTUATION,
                derived=True,
            )
        )
    folded = punct.casefold()
    folded = _collapse_whitespace(folded)
    tokens = tokenize_normalized(folded)
    if strip_suffixes:
        stripped_tokens, removed = strip_legal_suffixes(tokens)
        if removed:
            if record_aliases:
                aliases.append(
                    CompanyAlias(
                        canonical_candidate=" ".join(stripped_tokens),
                        variant=" ".join(tokens),
                        alias_kind=AliasKind.LEGAL_SUFFIX,
                        derived=True,
                    )
                )
            tokens = stripped_tokens
    normalized = " ".join(tokens)
    return normalized, tokens, tuple(aliases)


def names_match_exact(left_raw: str, right_raw: str) -> bool:
    """True only when full normalized token sequences are identical."""
    _, left_tokens, _ = normalize_legal_name(left_raw, record_aliases=False)
    _, right_tokens, _ = normalize_legal_name(right_raw, record_aliases=False)
    return bool(left_tokens) and left_tokens == right_tokens


def normalize_account_id(raw: str) -> str:
    """Normalize account identifier: NFKC, trim, uppercase ASCII letters."""
    step = unicodedata.normalize("NFKC", raw).strip()
    return step.upper()
