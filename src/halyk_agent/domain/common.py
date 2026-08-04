"""Common domain primitives."""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator, Field, StrictStr

NonEmptyStr = Annotated[StrictStr, Field(min_length=1)]

_CURRENCY_RE = re.compile(r"^[A-Za-z]{3}$")


def _normalize_currency(value: str) -> str:
    if not _CURRENCY_RE.fullmatch(value):
        raise ValueError("CurrencyCode must be exactly three ASCII letters")
    return value.upper()


CurrencyCode = Annotated[StrictStr, AfterValidator(_normalize_currency)]

type JsonPrimitive = str | int | float | bool | None
type JsonValue = JsonPrimitive | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]
