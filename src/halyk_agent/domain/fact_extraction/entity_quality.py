"""Entity-name quality gates for ownership / subsidiary facts."""

# ruff: noqa: RUF001

from __future__ import annotations

import re

_LEGAL_FORMS = frozenset(
    {
        "llp",
        "jsc",
        "inc",
        "llc",
        "ltd",
        "plc",
        "gmbh",
        "too",
        "тоо",
        "ао",
        "ооо",
        "ao",
        "zao",
        "зао",
        "пао",
        "pao",
    }
)
_LEGAL_FORM_STRIP = re.compile(
    r"[\s,.]*(?:LLP|JSC|Inc\.?|LLC|Ltd\.?|PLC|GmbH|ТОО|АО|ООО|TOO|ZAO|ЗАО|ПАО)\s*$",
    re.IGNORECASE,
)


def is_meaningful_entity_name(name: str) -> bool:
    """Reject entity_name that is only a legal form after stripping suffixes."""
    raw = name.strip(" ,.;:\t")
    if not raw:
        return False
    base = _LEGAL_FORM_STRIP.sub("", raw).strip(" ,.;:\t")
    if not base:
        return False
    if base.casefold() in _LEGAL_FORMS:
        return False
    alnum = re.sub(r"[^\w]", "", base, flags=re.UNICODE)
    return len(alnum) >= 2
