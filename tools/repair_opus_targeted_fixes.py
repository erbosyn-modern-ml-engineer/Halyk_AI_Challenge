"""Normalize temporary generated Opus fixes before verification.

Branch-only helper. Delete before merge.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def write(rel: str, text: str) -> None:
    (ROOT / rel).write_text(text, encoding="utf-8", newline="\n")


# Repair generated long-note regression literal.
rel = "tests/solver/test_competitive_fallbacks.py"
text = read(rel)
old = '+ ("narrative " * 400) + "\nRevaluations were recorded.\nNote 8 — Other\n"'
new = '+ ("narrative " * 400) + "\\nRevaluations were recorded.\\nNote 8 — Other\\n"'
if old not in text:
    raise RuntimeError("generated long-note literal not found")
write(rel, text.replace(old, new, 1))

# Extend Stage 5E malformed contract with comma+OCR confusable.
rel = "tests/facts/test_parse_money_contract.py"
text = read(rel)
marker = '        "$3OO,OOO",\n'
if marker not in text:
    raise RuntimeError("Stage 5E malformed-money marker not found")
write(rel, text.replace(marker, marker + '        "$5,OOO,000",\n', 1))

# Replace Stage 5E parser with complete-token behavior including suffix ISO notation.
rel = "src/halyk_agent/domain/fact_extraction/text_locate.py"
source = read(rel)
start = source.index("def parse_money(text: str) -> tuple[Decimal, str] | None:\n")
end = source.index("def parse_percentage(text: str) -> Decimal | None:\n", start)
replacement = textwrap.dedent(
    '''\
    def parse_money(text: str) -> tuple[Decimal, str] | None:
        """Parse the first complete money token without shorter-prefix recovery."""

        # Suffix ISO notation (``1 234,56 USD``) is legitimate, but it may
        # not rescue a malformed currency-prefixed token such as ``$300,00 USD``.
        suffix = _SUFFIX_MONEY_RE.search(text)
        if suffix is not None:
            before = text[: suffix.start()]
            prior_currency = re.search(
                r"[$€£¥₸]|\\b(?:USD|EUR|GBP|KZT|RUB|JPY)\\b", before, re.IGNORECASE
            )
            prefix = before.rstrip()
            bad_numeric_continuation = bool(
                prefix and (prefix[-1].isdigit() or prefix[-1] in ",.'`")
            )
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


    '''
)
source = source[:start] + replacement + source[end:]
source = source.replace("r\"(?<![\\w,.'’`])\"", "r\"(?<![\\w,.'`])\"")
write(rel, source)

# Harden complete money lexer against `$5,OOO,000` shorter-prefix acceptance
# and keep strict mypy typing explicit for the new threshold helper.
rel = "src/halyk_agent/domain/covenants/parse.py"
source = read(rel)
if "from decimal import Decimal\n" not in source:
    source = source.replace(
        "from datetime import date\n",
        "from datetime import date\nfrom decimal import Decimal\n",
        1,
    )
source = source.replace(
    "def _coerce_threshold_number(raw: str):\n",
    "def _coerce_threshold_number(raw: str) -> Decimal:\n",
    1,
)
old = '''            if nxt < n and rest[nxt] == ",":
                return _MoneyNumericParse(
                    "", _consume_malformed_money_tail(rest, 0), False
                )
            if nxt < n and _is_money_group_space(rest[nxt]):
'''
new = '''            if nxt < n and rest[nxt] == ",":
                return _MoneyNumericParse(
                    "", _consume_malformed_money_tail(rest, 0), False
                )
            if nxt < n and (rest[nxt].isalpha() or rest[nxt] in "._-'`"):
                return _MoneyNumericParse(
                    "", _consume_malformed_money_tail(rest, 0), False
                )
            if nxt < n and _is_money_group_space(rest[nxt]):
'''
if old not in source:
    raise RuntimeError("generated money comma guard not found")
write(rel, source.replace(old, new, 1))

# Normalize accidental duplicated dataclass and bind PPE row money only to its line.
rel = "src/halyk_agent/solver/fallbacks.py"
source = read(rel)
duplicate = (
    "@dataclass(frozen=True, slots=True)\n"
    "@dataclass(frozen=True, slots=True)\n"
)
if duplicate not in source:
    raise RuntimeError("generated duplicate fallback dataclass decorator not found")
source = source.replace(duplicate, "@dataclass(frozen=True, slots=True)\n", 1)
old = textwrap.dedent(
    '''\
    def _money_after_label(note: str, match: re.Match[str]) -> tuple[Decimal, str] | None:
        # Keep the window narrow enough that a missing value cannot bind to the next
        # row, while the money parser itself enforces complete-token semantics.
        return parse_money(note[match.end() : match.end() + 120])
    '''
)
new = textwrap.dedent(
    '''\
    def _money_after_label(note: str, match: re.Match[str]) -> tuple[Decimal, str] | None:
        # A PPE row may only bind to money on that same logical line. Searching
        # farther would let a malformed opening value borrow a later valid row.
        remainder = note[match.end() :]
        line = remainder.splitlines()[0] if remainder else ""
        return parse_money(line[:120])
    '''
)
if old not in source:
    raise RuntimeError("generated P5 money helper not found")
write(rel, source.replace(old, new, 1))

print("Opus targeted post-processing complete")
