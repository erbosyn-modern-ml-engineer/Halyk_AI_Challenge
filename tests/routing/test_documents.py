"""Document routing precedence and conflict tests."""

# ruff: noqa: RUF001

from __future__ import annotations

from halyk_agent.domain.routing.accounts import extract_account_identities
from halyk_agent.domain.routing.borrowers import extract_borrower_declarations
from halyk_agent.domain.routing.documents import route_documents
from halyk_agent.domain.routing.models import ResolutionMethod
from tests.routing.helpers import make_document


def test_exact_account_wins_over_name() -> None:
    doc = make_document(
        artifact="d1",
        raw_text=(
            "Shymkent Refinery Services JSC (joint stock company under Kazakhstan law) "
            "(далее — «Заёмщик»), имеющим банковский счёт ACC-7204 у Кредитора."
        ),
    )
    accounts = extract_account_identities(doc).accounts
    borrowers = extract_borrower_declarations(doc).borrowers
    assert borrowers, "expected borrower declaration extraction"
    bundle = route_documents(
        (doc,),
        account_extractions=accounts,
        borrowers=borrowers,
        scenario_accounts={"B4": frozenset({"ACC-7204"}), "P3": frozenset({"ACC-7803"})},
        borrower_name_by_scenario={
            "P3": frozenset({"shymkent refinery services"}),
            "B4": frozenset({"shymkent refinery"}),
        },
    )
    link = bundle.links[0]
    assert link.method is ResolutionMethod.EXPLICIT_ACCOUNT_ID
    assert link.scenario_ids == ("B4",)
    assert any(c.kind.value == "IDENTIFIER_NAME_CONFLICT" for c in bundle.conflicts)


def test_multi_account_document_conflict() -> None:
    doc = make_document(raw_text="See ACC-7801 and ACC-7802 together.")
    accounts = extract_account_identities(doc).accounts
    bundle = route_documents(
        (doc,),
        account_extractions=accounts,
        borrowers=(),
        scenario_accounts={"P1": frozenset({"ACC-7801"}), "P2": frozenset({"ACC-7802"})},
        borrower_name_by_scenario={},
    )
    assert bundle.multi_scenario_count == 1
    assert bundle.links[0].notes == "MULTI_SCENARIO_DOCUMENT"


def test_noise_document_unresolved() -> None:
    doc = make_document(raw_text="Cafeteria menu and parking policy. No bank identifiers.")
    bundle = route_documents(
        (doc,),
        account_extractions=(),
        borrowers=(),
        scenario_accounts={"P1": frozenset({"ACC-7801"})},
        borrower_name_by_scenario={},
    )
    assert bundle.unresolved_count == 1
    assert bundle.links[0].method is ResolutionMethod.UNRESOLVED
