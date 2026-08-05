"""Unit tests for PostgreSQL lexical OR/AND tsquery policy."""

from __future__ import annotations

import pytest

from halyk_agent.adapters.retrieval.postgres.lexical import (
    LEXICAL_POLICY_AND,
    LEXICAL_POLICY_OR,
    build_simple_tsquery,
)


def test_or_policy_joins_with_pipe() -> None:
    query = "лимит по договору"
    tsquery, tokens = build_simple_tsquery(query, policy=LEXICAL_POLICY_OR)
    assert "лимит" in tokens
    assert any(token.startswith("договор") for token in tokens)
    assert " | " in tsquery
    assert " & " not in tsquery


def test_and_policy_joins_with_ampersand() -> None:
    query = "лимит по договору"
    tsquery, tokens = build_simple_tsquery(query, policy=LEXICAL_POLICY_AND)
    assert "лимит" in tokens
    assert any(token.startswith("договор") for token in tokens)
    assert " & " in tsquery


def test_stopwords_removed_en_ru() -> None:
    _tsquery, tokens = build_simple_tsquery("the limit of the contract and по договору")
    assert "the" not in tokens
    assert "of" not in tokens
    assert "and" not in tokens
    assert "по" not in tokens
    assert "limit" in tokens
    assert "contract" in tokens
    assert any(token.startswith("договор") for token in tokens)


def test_empty_after_stopwords_raises() -> None:
    with pytest.raises(ValueError, match="non-stopword"):
        build_simple_tsquery("the and of по и")


def test_contract_id_lexeme_preserved() -> None:
    _tsquery, tokens = build_simple_tsquery("CTR-2024-01 limit")
    assert "ctr-2024-01" in tokens
