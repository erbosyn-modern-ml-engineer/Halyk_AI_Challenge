"""Deterministic character/token length estimation for chunking."""

from __future__ import annotations

# Stable heuristic used for estimated_token_count (not model-specific).
CHARS_PER_TOKEN = 4


def character_count(text: str) -> int:
    """Return Unicode code-point length (Python ``str`` length)."""
    return len(text)


def estimate_token_count(text: str) -> int:
    """Estimate tokens with a fixed characters-per-token ratio (ceil)."""
    if not text:
        return 0
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN
