"""Legal-name normalization tests."""

from __future__ import annotations

from halyk_agent.domain.routing.normalize import names_match_exact, normalize_legal_name


def test_unicode_and_punctuation_normalization() -> None:
    normalized, tokens, aliases = normalize_legal_name("  «Shymkent   Refinery»  JSC ")
    assert normalized == "shymkent refinery"
    assert tokens == ("shymkent", "refinery")
    assert aliases


def test_legal_suffix_and_full_token_equality() -> None:
    assert names_match_exact("Shymkent Refinery JSC", "shymkent refinery")
    assert not names_match_exact("Shymkent Refinery", "Shymkent Refinery Services")
    assert not names_match_exact("Ekibastuz Energy", "Ekibastuz Power Services")


def test_near_collisions_do_not_match() -> None:
    left, _, _ = normalize_legal_name("Shymkent Refinery JSC")
    right, _, _ = normalize_legal_name("Shymkent Refinery Services JSC")
    assert left != right
    e1, _, _ = normalize_legal_name("Ekibastuz Energy JSC")
    e2, _, _ = normalize_legal_name("Ekibastuz Power Services JSC")
    assert e1 != e2
