"""Deterministic length-prefixed SHA-256 identifiers."""

from __future__ import annotations

import hashlib


def _length_prefixed(parts: list[str | bytes | int | float | None]) -> bytes:
    """Encode components with unambiguous length prefixes."""
    encoded = bytearray()
    for part in parts:
        if part is None:
            encoded.extend(b"\x00")
            continue
        if isinstance(part, bytes):
            raw = part
        elif isinstance(part, bool):
            raw = b"1" if part else b"0"
        elif isinstance(part, int):
            raw = str(part).encode("ascii")
        elif isinstance(part, float):
            raw = format(part, ".12g").encode("ascii")
        else:
            raw = str(part).encode("utf-8")
        encoded.extend(f"{len(raw)}:".encode("ascii"))
        encoded.extend(raw)
        encoded.extend(b"|")
    return bytes(encoded)


def deterministic_id(*parts: str | bytes | int | float | None) -> str:
    """Return a hex SHA-256 over length-prefixed components."""
    return hashlib.sha256(_length_prefixed(list(parts))).hexdigest()


def sha256_text(value: str) -> str:
    """SHA-256 hex digest of UTF-8 text."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
