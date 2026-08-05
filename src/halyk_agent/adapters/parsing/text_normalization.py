"""Text normalization that preserves raw parser output separately."""

from __future__ import annotations

import unicodedata

NORMALIZATION_VERSION = "halyk.text_normalization.v1"


def normalize_text(
    raw_text: str,
    *,
    collapse_internal_whitespace: bool = False,
) -> str:
    """Produce normalized_text without overwriting raw_text semantics.

    - Unicode NFC
    - line endings -> ``\\n``
    - NBSP -> space
    - strip NUL
    - trim trailing whitespace per line
    - optionally collapse excessive internal whitespace
    """
    text = raw_text.replace("\x00", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ")
    text = unicodedata.normalize("NFC", text)
    lines = [line.rstrip() for line in text.split("\n")]
    if collapse_internal_whitespace:
        collapsed: list[str] = []
        for line in lines:
            parts = line.split()
            collapsed.append(" ".join(parts))
        lines = collapsed
    return "\n".join(lines)
