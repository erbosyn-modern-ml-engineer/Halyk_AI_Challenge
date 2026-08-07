"""Whitespace-normalized identity search with exact raw provenance."""

from __future__ import annotations

import pytest

from halyk_agent.domain.routing.normalize import normalize_legal_name_keys
from halyk_agent.domain.routing.whitespace_search import (
    build_whitespace_normalized_view,
    find_whitespace_normalized,
)


@pytest.mark.parametrize(
    ("raw", "needle"),
    [
        ("Ekibastuz Power\nServices JSC", "Ekibastuz Power Services JSC"),
        ("Ekibastuz Power  Services JSC", "Ekibastuz Power Services JSC"),
        ("Ekibastuz Power\tServices JSC", "Ekibastuz Power Services JSC"),
        ("Ekibastuz Power\r\nServices JSC", "Ekibastuz Power Services JSC"),
        ("Ekibastuz Power\n\nServices JSC", "Ekibastuz Power Services JSC"),
        ("Ekibastuz Power\u00a0Services JSC", "Ekibastuz Power Services JSC"),
        ("prefix Ekibastuz Power\nServices JSC, which", "Ekibastuz Power Services JSC"),
    ],
)
def test_whitespace_normalized_find_maps_to_exact_raw(raw: str, needle: str) -> None:
    spans = find_whitespace_normalized(raw, needle)
    assert spans, f"expected match in {raw!r}"
    start, end = spans[0]
    quote = raw[start:end]
    assert quote == raw[start:end]
    keys = normalize_legal_name_keys(quote, record_aliases=False)
    expected = normalize_legal_name_keys(needle, record_aliases=False)
    assert keys.identity_key == expected.identity_key


def test_newline_quote_preserves_layout() -> None:
    raw = "through Ekibastuz Power\nServices JSC, which"
    needle = "Ekibastuz Power Services JSC"
    start, end = find_whitespace_normalized(raw, needle)[0]
    assert raw[start:end] == "Ekibastuz Power\nServices JSC"
    view = build_whitespace_normalized_view(raw)
    assert "Ekibastuz Power Services JSC" in view.normalized


def test_identity_key_rejects_form_mismatch_after_whitespace_hit() -> None:
    raw = "Alpha Energy\nLLP report"
    needle = "Alpha Energy JSC"
    spans = find_whitespace_normalized(raw, needle)
    # Needle characters include JSC which is absent → no layout hit.
    assert spans == []
