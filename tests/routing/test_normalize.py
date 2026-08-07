"""Legal-name normalization tests (Stage 5B.1 identity_key semantics)."""

from __future__ import annotations

from halyk_agent.domain.routing.normalize import (
    legal_form_mismatch,
    names_match_exact,
    normalize_legal_name,
    normalize_legal_name_keys,
)


def test_unicode_and_punctuation_normalization() -> None:
    result = normalize_legal_name_keys("  «Shymkent   Refinery»  JSC ")
    assert result.identity_key == "shymkent refinery jsc"
    assert result.base_key == "shymkent refinery"
    assert result.aliases


def test_legal_forms_remain_distinct() -> None:
    assert not names_match_exact("Aktau Port Services JSC", "Aktau Port Services LLP")
    assert not names_match_exact("Almaty Cold Chain JSC", "Almaty Cold Chain TOO")
    assert not names_match_exact("Shymkent Refinery JSC", "Shymkent Refinery LLP")
    assert not names_match_exact("Shymkent Refinery Services JSC", "Shymkent Refinery Services LLP")
    assert legal_form_mismatch("Aktau Port Services JSC", "Aktau Port Services LLP")


def test_near_collisions_and_full_token_equality() -> None:
    assert not names_match_exact("Shymkent Refinery", "Shymkent Refinery Services")
    assert not names_match_exact("Ekibastuz Energy", "Ekibastuz Power Services")
    assert not names_match_exact("ABC Energy", "ABC Energy Services")
    assert not names_match_exact("Foo Mining", "Foo Mining Kazakhstan")
    assert not names_match_exact("A-B JSC", "AB JSC")
    # Same identity including legal form
    assert names_match_exact("Shymkent Refinery JSC", "shymkent refinery jsc")
    # base_key alone must never accept
    left = normalize_legal_name_keys("Shymkent Refinery JSC")
    right = normalize_legal_name_keys("Shymkent Refinery LLP")
    assert left.base_key == right.base_key
    assert left.identity_key != right.identity_key


def test_compat_wrapper_defaults_to_identity_key() -> None:
    key, tokens, _ = normalize_legal_name("Aktau Port Services JSC")
    assert key == "aktau port services jsc"
    assert tokens[-1] == "jsc"
