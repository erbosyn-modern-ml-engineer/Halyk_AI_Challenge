"""Locate exact quotes and parse money / percent / TXN tokens in page text."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from halyk_agent.domain.covenants.parse import scan_money_quantities
from halyk_agent.domain.parsing import CanonicalDocument

TXN_ID_RE = re.compile(r"\bTXN-[A-Za-z0-9]+-\d+\b")

# $1,234.56 | €918.00 | USD 1,234.56 | 1 234,56 USD (common RU/EN forms)
_MONEY_RE = re.compile(
    r"(?P<sym>\$|€|£|¥)|"
    r"(?:(?P<code>USD|EUR|GBP|KZT|RUB)\s*)?"
    r"(?P<num>(?:\d{1,3}(?:[ ,]\d{3})+|\d+)(?:[.,]\d{1,2})?)"
    r"(?:\s*(?P<code2>USD|EUR|GBP|KZT|RUB))?",
    re.IGNORECASE,
)

_PERCENT_RE = re.compile(
    r"(?P<num>\d+(?:[.,]\d+)?)\s*%|"
    r"(?P<num2>\d+(?:[.,]\d+)?)\s*(?:процент|percent|pct)\b",
    re.IGNORECASE,
)

_SYM_CURRENCY = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}
_SUFFIX_MONEY_RE = re.compile(
    r"(?<![\w,.'`])"
    r"(?P<num>(?:\d{1,3}(?:[ \xa0\u202f\u2009]\d{3})+|\d{1,3}(?:,\d{3})+|\d+)"
    r"(?:[.,]\d{1,2})?)\s*(?P<code>USD|EUR|GBP|KZT|RUB|JPY)\b",
    re.IGNORECASE,
)


def find_quote_offsets(
    document: CanonicalDocument,
    quote: str,
) -> tuple[int, int, int] | None:
    """
    Find the first exact occurrence of ``quote`` in page raw text.

    Returns ``(page_number, char_start, char_end)`` or None.
    """
    needle = quote
    if not needle:
        return None
    for page in sorted(document.pages, key=lambda item: item.page_number):
        text = page.raw_text or ""
        idx = text.find(needle)
        if idx >= 0:
            return page.page_number, idx, idx + len(needle)
    return None


def _normalize_number(raw: str) -> Decimal:
    cleaned = raw.strip()
    for separator in ("\xa0", "\u202f", "\u2009", " ", "\t"):
        cleaned = cleaned.replace(separator, "")
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        parts = cleaned.split(",")
        if len(parts) == 2 and len(parts[1]) <= 2:
            cleaned = cleaned.replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"invalid money/percent number: {raw!r}") from exc


def parse_money(text: str) -> tuple[Decimal, str] | None:
    """Parse the first complete money token without shorter-prefix recovery."""

    # Suffix ISO notation (``1 234,56 USD``) is legitimate, but it may
    # not rescue a malformed currency-prefixed token such as ``$300,00 USD``.
    suffix = _SUFFIX_MONEY_RE.search(text)
    if suffix is not None:
        before = text[: suffix.start()]
        prior_currency = re.search(
            r"[$€£¥₸]|\b(?:USD|EUR|GBP|KZT|RUB|JPY)\b", before, re.IGNORECASE
        )
        prefix = before.rstrip()
        bad_numeric_continuation = bool(prefix and (prefix[-1].isdigit() or prefix[-1] in ",.'`"))
        if prior_currency is None and not bad_numeric_continuation:
            try:
                return _normalize_number(suffix.group("num")), suffix.group("code").upper()
            except ValueError:
                return None

    scan = scan_money_quantities(text)
    if scan.has_malformed:
        return None
    if scan.quantities:
        quantity = scan.quantities[0]
        if quantity.currency is None:
            return None
        return quantity.value, quantity.currency
    return None


def parse_percentage(text: str) -> Decimal | None:
    """Parse the first percentage token in ``text``."""
    match = _PERCENT_RE.search(text)
    if match is None:
        return None
    raw = match.group("num") or match.group("num2")
    if raw is None:
        return None
    try:
        return _normalize_number(raw)
    except ValueError:
        return None


def find_txn_ids(text: str) -> tuple[str, ...]:
    """Return all TXN-… identifiers in document order (deduped, stable)."""
    seen: set[str] = set()
    out: list[str] = []
    for match in TXN_ID_RE.finditer(text):
        value = match.group(0)
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return tuple(out)


def page_text_slices(
    document: CanonicalDocument,
) -> tuple[tuple[int, str], ...]:
    """Return ``(page_number, raw_text)`` pairs sorted by page number."""
    return tuple(
        (page.page_number, page.raw_text or "")
        for page in sorted(document.pages, key=lambda item: item.page_number)
    )
