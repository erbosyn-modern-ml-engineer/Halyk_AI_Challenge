"""Ownership semantic table-context regressions (Stage 5E.3)."""

# ruff: noqa: RUF001

from __future__ import annotations

from decimal import Decimal

import pytest

from halyk_agent.domain.authority.models import AuthorityDomain
from halyk_agent.domain.fact_extraction.entity_quality import is_meaningful_entity_name
from halyk_agent.domain.fact_extraction.extractors import extract_candidates
from halyk_agent.domain.fact_extraction.models import FactKind, OwnershipPayload
from halyk_agent.domain.fact_extraction.ownership_context import ownership_context_reason
from halyk_agent.domain.models_gateway.providers.deepseek import DeepSeekStructuredProvider
from tests.authority.helpers import make_document
from tests.facts.helpers import make_requirement


def _req():
    return make_requirement(
        FactKind.OWNERSHIP,
        "владе",
        domain=AuthorityDomain.KYC_RELATIONSHIPS,
    )


def test_collateral_table_emits_zero_ownership() -> None:
    text = (
        "Дочерняя организация Доля активов в залоге\n"
        "Example Conveyor Assets LLP 87.6%\n"
        "Example Processing Holdings LLP 11.4%\n"
    )
    doc = make_document(raw_text=text)
    assert extract_candidates(_req(), doc) == []


def test_voting_rights_table_emits_ownership() -> None:
    text = (
        "Акционер / участник Доля голосующих прав\n"
        "Example Capital LLP 39.7%\n"
        "Example Holdings LLP 31.4%\n"
    )
    doc = make_document(raw_text=text)
    cands = extract_candidates(_req(), doc)
    assert len(cands) == 2
    payloads = {c.payload.entity_name: c.payload for c in cands}
    assert isinstance(payloads["Example Capital LLP"], OwnershipPayload)
    assert payloads["Example Capital LLP"].ownership_percent == Decimal("39.7")
    assert payloads["Example Holdings LLP"].ownership_percent == Decimal("31.4")
    assert all(getattr(c.payload, "voting_rights", True) for c in cands)


def test_both_tables_same_page_only_voting_rows() -> None:
    text = (
        "Организация Доля голосующих прав\n"
        "Vote Alpha LLP 39.7%\n"
        "Vote Beta LLP 31.4%\n"
        "\n"
        "Дочерняя организация Доля активов в залоге\n"
        "Pledge Alpha LLP 87.6%\n"
        "Pledge Beta LLP 11.4%\n"
    )
    doc = make_document(raw_text=text)
    cands = extract_candidates(_req(), doc)
    names = {c.payload.entity_name for c in cands}
    assert names == {"Vote Alpha LLP", "Vote Beta LLP"}
    assert "Pledge Alpha LLP" not in names


def test_stale_ownership_header_does_not_legitimize_collateral_rows() -> None:
    text = (
        "Организация Доля голосующих прав\n"
        "Old Owner LLP 10.0%\n"
        "\n" * 5 + "Обеспечительное покрытие дочерних организаций\n"
        "Дочерняя организация Доля активов в залоге\n"
        "Collateral Row LLP 87.6%\n"
    )
    doc = make_document(raw_text=text)
    cands = extract_candidates(_req(), doc)
    names = {c.payload.entity_name for c in cands}
    assert "Old Owner LLP" in names
    assert "Collateral Row LLP" not in names


def test_mojibake_voting_header_recovers_rows() -> None:
    # UTF-8 interpreted as cp1251 style header for "Доля голосующих прав"
    header = "Организация Доля голосующих прав".encode().decode("cp1251")
    text = f"{header}\nAlmaty Chill Logistics LLP 8.6%\nTien Shan Advisory Bureau 23.4%\n"
    doc = make_document(raw_text=text)
    cands = extract_candidates(_req(), doc)
    assert len(cands) >= 2
    names = {c.payload.entity_name for c in cands}
    assert "Almaty Chill Logistics LLP" in names


def test_mojibake_collateral_header_rejected() -> None:
    header = "Дочерняя организация Доля активов в залоге".encode().decode("cp1251")
    text = f"{header}\nZhezkazgan Conveyor Assets LLP 87.6%\n"
    doc = make_document(raw_text=text)
    assert extract_candidates(_req(), doc) == []


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("LLP", False),
        ('"LLP"', False),
        ("'LLP'", False),
        ("«LLP»", False),
        ("JSC", False),
        ('"JSC"', False),
        ("ТОО", False),
        ("«ТОО»", False),
        ('"Example Holdings" LLP', True),
        ("«Example Capital» JSC", True),
        ("Example Advisory Bureau LLP", True),
    ],
)
def test_entity_quality_quoted_legal_forms(name: str, expected: bool) -> None:
    assert is_meaningful_entity_name(name) is expected


def test_context_reason_is_local() -> None:
    page = "Организация Доля голосующих прав\nA LLP 10%\nДоля активов в залоге\nB LLP 90%\n"
    a_idx = page.index("A LLP")
    b_idx = page.index("B LLP")
    assert ownership_context_reason(page, a_idx) is not None
    assert ownership_context_reason(page, b_idx) is None


def test_deepseek_examples_have_no_public_corpus_data() -> None:
    from halyk_agent.domain.models_gateway.providers import deepseek as deepseek_mod

    banned = (
        "Saryarka",
        "Tengiz",
        "Zhezkazgan",
        "Almaty Chill",
        "Tien Shan",
        "Ulytau",
        "ACC-78",
        "TXN-P",
        "TXN-B",
        "42.3",
        "118447",
        "884204",
        "918447",
    )
    for kind, example in deepseek_mod._FACT_JSON_EXAMPLES.items():
        blob = str(example)
        for token in banned:
            assert token not in blob, f"{kind} example contains public token {token}"
    provider = DeepSeekStructuredProvider(thinking_enabled=False)
    ownership = provider.json_example_for("OWNERSHIP")
    assert "Northbridge" in str(ownership)
    assert "37.5" in str(ownership)
