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


def _canonical_legal_form_token(token: str) -> str | None:
    """Return the legal-form class for one compact token variant."""

    stripped = token.strip(",").rstrip(".")
    if stripped in _LEGAL_FORM_CANON:
        return _LEGAL_FORM_CANON[stripped]
    compact = stripped.replace(".", "")
    return _LEGAL_FORM_CANON.get(compact)


def _trim_comma_before_legal_form(tokens: tuple[str, ...]) -> tuple[tuple[str, ...], bool]:
    """Ignore only a comma immediately before the legal-form token."""

    if not tokens or not tokens[-1].endswith(","):
        return tokens, False
    cleaned = tokens[-1].rstrip(",")
    if not cleaned:
        return tokens[:-1], True
    return (*tokens[:-1], cleaned), True


def _extract_legal_form(
    tokens: tuple[str, ...],
) -> tuple[tuple[str, ...], str | None, bool]:
    """Peel a legal-form token while preserving its identity class.

    Legal form may appear as a suffix or a prefix. Only same-class punctuation /
    position variants are normalized; distinct legal forms remain distinct.
    """
    if not tokens:
        return tokens, None, False

    # Existing/common suffix form: "Foo LLP", "Foo LLP.", "Foo L.L.P.".
    suffix_form = _canonical_legal_form_token(tokens[-1])
    if suffix_form is not None:
        body, comma_normalized = _trim_comma_before_legal_form(tokens[:-1])
        normalized = comma_normalized or tokens[-1] != suffix_form
        return body, suffix_form, normalized

    # Spaced dotted suffix after punctuation spacing: ("l.", "l.", "p.").
    if len(tokens) >= 3:
        tail = "".join(tok.strip(",").rstrip(".") for tok in tokens[-3:])
        if tail in _LEGAL_FORM_CANON:
            body, _ = _trim_comma_before_legal_form(tokens[:-3])
            return body, _LEGAL_FORM_CANON[tail], True

    # Prefix legal form is an ordering variant, not a different entity.
    prefix_form = _canonical_legal_form_token(tokens[0])
    if prefix_form is not None and len(tokens) > 1:
        return tokens[1:], prefix_form, True

    if len(tokens) >= 3:
        head = "".join(tok.strip(",").rstrip(".") for tok in tokens[:3])
        if head in _LEGAL_FORM_CANON and len(tokens) > 3:
            return tokens[3:], _LEGAL_FORM_CANON[head], True

    return tokens, None, False


def normalize_legal_name_keys(
    raw: str,
    *,
    record_aliases: bool = True,
) -> NormalizedLegalName:
    """Build identity_key (form-preserving) and base_key (form-stripped)."""
    folded, aliases = _preprocess(raw)
    tokens = tokenize_normalized(folded)
    base_tokens, legal_form, punct_normalized = _extract_legal_form(tokens)
    if legal_form is None:
        identity_tokens = tokens
        base_tokens = tokens
    else:
        identity_tokens = (*base_tokens, legal_form)
        if record_aliases and (punct_normalized or tokens[-1:] != (legal_form,)):
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
    """Compatibility wrapper.

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
