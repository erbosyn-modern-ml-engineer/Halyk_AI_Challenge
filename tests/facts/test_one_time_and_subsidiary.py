"""Stage 5E extractors for one-time add-backs and security-perimeter status."""

# ruff: noqa: RUF001

from __future__ import annotations

from decimal import Decimal

from halyk_agent.domain.authority.models import AuthorityDomain
from halyk_agent.domain.fact_extraction.extractors import (
    extract_group_capex,
    extract_one_time_add_back,
    extract_subsidiary_status,
)
from halyk_agent.domain.fact_extraction.models import (
    FactKind,
    GroupCapexDerivationType,
    SubsidiaryDerivationType,
    SubsidiaryKind,
)
from tests.authority.helpers import make_document
from tests.facts.helpers import make_requirement


def test_one_time_table_extracts_below_materiality() -> None:
    text = (
        "Ниже приведены разовые статьи, выявленные в ходе проверки.\n"
        "Очистка дна «Zhaiyk Dredging LLP» $251,338.94\n"
        "Урегулирование спора «Aga Freight Arbitration Bureau» $342,905.28\n"
        "Разовыми для целей ковенантов признаются статьи в сумме не менее $300,000.00\n"
    )
    doc = make_document(raw_text=text)
    hits = extract_one_time_add_back(
        make_requirement(FactKind.ONE_TIME_ADD_BACK, "разов"),
        doc,
        AuthorityDomain.FINANCIAL_ADJUSTMENTS,
    )
    amounts = sorted(h.payload.amount.value for h in hits)  # type: ignore[union-attr]
    assert Decimal("251338.94") in amounts
    assert Decimal("342905.28") in amounts
    # Materiality floor is NOT applied in Stage 5E.
    assert Decimal("300000.00") not in amounts


def test_security_perimeter_subsidiary_status() -> None:
    text = (
        "Дочерняя организация Доля активов в залоге\n"
        "Alpha Conveyor Assets LLP 87.6%\n"
        "Beta Processing Holdings LLP 11.4%\n"
        "Дочерние организации, у которых доля активов в залоге ниже 50.0%, "
        "находятся вне периметра обеспечения и для целей Договора "
        "рассматриваются как неограниченные.\n"
    )
    doc = make_document(raw_text=text)
    hits = extract_subsidiary_status(
        make_requirement(
            FactKind.SUBSIDIARY_STATUS,
            "дочерн",
            domain=AuthorityDomain.KYC_RELATIONSHIPS,
        ),
        doc,
        AuthorityDomain.KYC_RELATIONSHIPS,
    )
    by_name = {h.payload.entity_name: h.payload for h in hits}  # type: ignore[union-attr]
    assert by_name["Alpha Conveyor Assets LLP"].status is SubsidiaryKind.RESTRICTED
    assert by_name["Beta Processing Holdings LLP"].status is SubsidiaryKind.UNRESTRICTED
    assert (
        by_name["Beta Processing Holdings LLP"].derivation_type
        is SubsidiaryDerivationType.SECURITY_PERIMETER_THRESHOLD
    )


def test_group_capex_refuses_open_roll_forward() -> None:
    text = (
        "Note 7 — PPE\n"
        "Net book value at the beginning of the year $148,028,989.69\n"
        "Depreciation charge for the year $15,826,229.43\n"
        "Net book value at the end of the year $154,050,122.81\n"
    )
    doc = make_document(raw_text=text)
    hits = extract_group_capex(
        make_requirement(FactKind.GROUP_CAPEX, "ppe", domain=AuthorityDomain.GROUP_STRUCTURE),
        doc,
        AuthorityDomain.GROUP_STRUCTURE,
    )
    assert hits == []


def test_group_capex_closed_roll_forward() -> None:
    text = (
        "Property, plant and equipment\n"
        "Net book value at the beginning of the year $100.00\n"
        "Depreciation charge for the year $20.00\n"
        "Net book value at the end of the year $130.00\n"
        "There were no disposals, transfers, impairments or revaluations.\n"
    )
    doc = make_document(raw_text=text)
    hits = extract_group_capex(
        make_requirement(FactKind.GROUP_CAPEX, "ppe", domain=AuthorityDomain.GROUP_STRUCTURE),
        doc,
        AuthorityDomain.GROUP_STRUCTURE,
    )
    assert len(hits) == 1
    payload = hits[0].payload
    assert payload.derivation_type is GroupCapexDerivationType.PPE_ROLL_FORWARD  # type: ignore[union-attr]
    assert payload.amount.value == Decimal("50.00")  # type: ignore[union-attr]
