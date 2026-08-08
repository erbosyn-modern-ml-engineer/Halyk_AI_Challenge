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
    assert names_match_exact("Shymkent Refinery JSC", "shymkent refinery jsc")
    left = normalize_legal_name_keys("Shymkent Refinery JSC")
    right = normalize_legal_name_keys("Shymkent Refinery LLP")
    assert left.base_key == right.base_key
    assert left.identity_key != right.identity_key


def test_compat_wrapper_defaults_to_identity_key() -> None:
    key, tokens, _ = normalize_legal_name("Aktau Port Services JSC")
    assert key == "aktau port services jsc"
    assert tokens[-1] == "jsc"


def test_legal_form_punctuation_variants_match() -> None:
    assert names_match_exact("Kazyna Capital LLP", "Kazyna Capital LLP.")
    assert names_match_exact("Kazyna Capital LLP", "Kazyna Capital L.L.P.")
    assert names_match_exact("Kazyna Capital LLP.", "Kazyna Capital L.L.P.")
    assert names_match_exact("Alpha Energy JSC", "Alpha Energy J.S.C.")
    assert names_match_exact("Alpha Energy JSC", "Alpha Energy J.S.C")


def test_legal_form_position_and_boundary_separator_variants_match() -> None:
    assert names_match_exact("Astana Trade Holding LLP", "LLP Astana Trade Holding")
    assert names_match_exact("Astana Trade Holding LLP", "Astana Trade Holding, LLP")
    assert names_match_exact("Astana Trade Holding LLP", "LLP, Astana Trade Holding")
    cyrillic_suffix = "\u0410\u0441\u0442\u0430\u043d\u0430 \u0422\u0440\u0435\u0439\u0434 \u0422\u041e\u041e"
    cyrillic_prefix = "\u0422\u041e\u041e «\u0410\u0441\u0442\u0430\u043d\u0430 \u0422\u0440\u0435\u0439\u0434»"
    assert names_match_exact(cyrillic_suffix, cyrillic_prefix)


def test_position_normalization_never_collapses_distinct_legal_forms_or_qualifiers() -> None:
    assert not names_match_exact("LLP Astana Trade Holding", "JSC Astana Trade Holding")
    assert not names_match_exact("Astana Trade Holding LLP", "Astana Trade Holding Branch LLP")


def test_legal_form_classes_remain_distinct_under_punctuation() -> None:
    assert not names_match_exact("Alpha Energy JSC", "Alpha Energy LLP")
    assert not names_match_exact("Alpha Energy J.S.C.", "Alpha Energy L.L.P.")
    assert not names_match_exact("Alpha Energy JSC", "Alpha Energy TOO")
    assert not names_match_exact("Alpha Energy LLP", "Alpha Energy TOO")
    assert not names_match_exact("Alpha Energy JSC", "Alpha Energy Services JSC")
