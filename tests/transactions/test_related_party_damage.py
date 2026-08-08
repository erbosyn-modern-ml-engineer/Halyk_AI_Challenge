"""Conservative damaged related-party identity recovery regressions."""

from __future__ import annotations

from decimal import Decimal

from halyk_agent.domain.transaction_taxonomy.related_party import (
    DamagedOwnershipEntity,
    possible_damaged_identity_match,
    recover_unique_damaged_related_parties,
)


def _damaged(name: str = "Astana Tr?de Holding LLP") -> DamagedOwnershipEntity:
    return DamagedOwnershipEntity(
        scenario_id="P6",
        entity_name=name,
        ownership_percent=Decimal("30"),
        threshold_percent=Decimal("25"),
        fact_ids=("threshold", "ownership"),
    )


def test_mid_token_damage_remains_one_wildcard_token() -> None:
    assert possible_damaged_identity_match(
        "Astana Tr?de Holding LLP", "Astana Trade Holding LLP"
    )
    assert not possible_damaged_identity_match(
        "Astana Tr?de Holding LLP", "Astana Trade Holding JSC"
    )
    assert not possible_damaged_identity_match(
        "Astana Tr?de Holding LLP", "Astana North Trade Holding LLP"
    )


def test_unique_mid_token_candidate_is_recovered() -> None:
    recovered = recover_unique_damaged_related_parties(
        (_damaged(),),
        {"P6": ("Astana Trade Holding LLP",)},
    )
    assert len(recovered) == 1
    assert recovered[0].entity_name == "Astana Trade Holding LLP"
    assert recovered[0].identity_key == "astana trade holding llp"


def test_two_plausible_mid_token_candidates_remain_ambiguous() -> None:
    recovered = recover_unique_damaged_related_parties(
        (_damaged(),),
        {"P6": ("Astana Trade Holding LLP", "Astana Tride Holding LLP")},
    )
    assert recovered == ()


def test_one_candidate_cannot_repair_two_damaged_owners() -> None:
    recovered = recover_unique_damaged_related_parties(
        (
            _damaged("Astana Tr?de Holding LLP"),
            _damaged("Astana T?ade Holding LLP"),
        ),
        {"P6": ("Astana Trade Holding LLP",)},
    )
    assert recovered == ()
