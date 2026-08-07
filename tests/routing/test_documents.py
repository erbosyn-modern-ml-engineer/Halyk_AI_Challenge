"""Document routing precedence and conflict tests."""

# ruff: noqa: RUF001

from __future__ import annotations

from halyk_agent.domain.routing.accounts import extract_account_identities
from halyk_agent.domain.routing.borrowers import extract_borrower_declarations
from halyk_agent.domain.routing.documents import route_documents
from halyk_agent.domain.routing.models import ResolutionMethod
from tests.routing.helpers import make_document


def _route(doc, *, accounts, borrowers, scenario_accounts, identities, raws=None):
    return route_documents(
        (doc,),
        account_extractions=accounts,
        borrowers=borrowers,
        scenario_accounts=scenario_accounts,
        borrower_identity_by_scenario=identities,
        borrower_raw_by_identity=raws or {},
    )


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
    bundle = _route(
        doc,
        accounts=accounts,
        borrowers=borrowers,
        scenario_accounts={"B4": frozenset({"ACC-7204"}), "P3": frozenset({"ACC-7803"})},
        identities={
            "P3": frozenset({"shymkent refinery services jsc"}),
            "B4": frozenset({"shymkent refinery jsc"}),
        },
        raws={
            "shymkent refinery services jsc": ("Shymkent Refinery Services JSC",),
            "shymkent refinery jsc": ("Shymkent Refinery JSC",),
        },
    )
    link = bundle.links[0]
    assert link.method is ResolutionMethod.EXPLICIT_ACCOUNT_ID
    assert link.scenario_ids == ("B4",)
    assert any(c.kind.value == "IDENTIFIER_NAME_CONFLICT" for c in bundle.conflicts)


def test_multi_account_document_conflict() -> None:
    doc = make_document(raw_text="See ACC-7801 and ACC-7802 together.")
    accounts = extract_account_identities(doc).accounts
    bundle = _route(
        doc,
        accounts=accounts,
        borrowers=(),
        scenario_accounts={"P1": frozenset({"ACC-7801"}), "P2": frozenset({"ACC-7802"})},
        identities={},
    )
    assert bundle.multi_scenario_count == 1
    assert bundle.links[0].notes == "MULTI_SCENARIO_DOCUMENT"


def test_noise_document_unresolved() -> None:
    doc = make_document(raw_text="Cafeteria menu and parking policy. No bank identifiers.")
    bundle = _route(
        doc,
        accounts=(),
        borrowers=(),
        scenario_accounts={"P1": frozenset({"ACC-7801"})},
        identities={},
    )
    assert bundle.unresolved_count == 1
    assert bundle.links[0].method is ResolutionMethod.UNRESOLVED


def test_level4_letterhead_exact_name_routes() -> None:
    doc = make_document(
        artifact="letter",
        raw_text="Shymkent Refinery Services JSC\nInternal brand guide — 2025",
    )
    bundle = _route(
        doc,
        accounts=(),
        borrowers=(),
        scenario_accounts={"P3": frozenset({"ACC-7803"})},
        identities={"P3": frozenset({"shymkent refinery services jsc"})},
        raws={"shymkent refinery services jsc": ("Shymkent Refinery Services JSC",)},
    )
    assert bundle.links[0].method is ResolutionMethod.NORMALIZED_LEGAL_NAME
    assert bundle.links[0].scenario_ids == ("P3",)
    assert bundle.links[0].evidence_span_ids


def test_group_segment_declaration_routes() -> None:
    doc = make_document(
        artifact="group",
        raw_text=(
            "Note 6 — Segment Information.\n"
            "The Group's generation segment is conducted through Alpha Energy JSC,\n"
            "which operates as a standalone subsidiary."
        ),
    )
    bundle = _route(
        doc,
        accounts=(),
        borrowers=(),
        scenario_accounts={"P9": frozenset({"ACC-7809"})},
        identities={"P9": frozenset({"alpha energy jsc"})},
        raws={"alpha energy jsc": ("Alpha Energy JSC",)},
    )
    link = bundle.links[0]
    assert link.method is ResolutionMethod.GROUP_SEGMENT_DECLARATION
    assert link.group_document is True
    assert link.scenario_ids == ("P9",)
    assert link.evidence_span_ids


def test_group_false_positive_boilerplate_falls_to_level4() -> None:
    doc = make_document(
        artifact="press",
        raw_text=("About Alpha Energy JSC: the group operates across its key regional markets."),
    )
    bundle = _route(
        doc,
        accounts=(),
        borrowers=(),
        scenario_accounts={"P9": frozenset({"ACC-7809"})},
        identities={"P9": frozenset({"alpha energy jsc"})},
        raws={"alpha energy jsc": ("Alpha Energy JSC",)},
    )
    link = bundle.links[0]
    assert link.method is ResolutionMethod.NORMALIZED_LEGAL_NAME
    assert link.group_document is False
    assert link.scenario_ids == ("P9",)


def test_bare_segment_not_group_relation() -> None:
    doc = make_document(
        artifact="seg",
        raw_text="Alpha Energy JSC — segment update",
    )
    bundle = _route(
        doc,
        accounts=(),
        borrowers=(),
        scenario_accounts={"P9": frozenset({"ACC-7809"})},
        identities={"P9": frozenset({"alpha energy jsc"})},
        raws={"alpha energy jsc": ("Alpha Energy JSC",)},
    )
    link = bundle.links[0]
    assert link.method is ResolutionMethod.NORMALIZED_LEGAL_NAME
    assert link.group_document is False


def test_group_relation_multi_borrower_conflict() -> None:
    doc = make_document(
        artifact="multi",
        raw_text=(
            "The generation segment is conducted through Alpha Energy JSC "
            "and Beta Power JSC as wholly owned subsidiaries."
        ),
    )
    bundle = _route(
        doc,
        accounts=(),
        borrowers=(),
        scenario_accounts={
            "P9": frozenset({"ACC-7809"}),
            "P8": frozenset({"ACC-7808"}),
        },
        identities={
            "P9": frozenset({"alpha energy jsc"}),
            "P8": frozenset({"beta power jsc"}),
        },
        raws={
            "alpha energy jsc": ("Alpha Energy JSC",),
            "beta power jsc": ("Beta Power JSC",),
        },
    )
    assert bundle.multi_scenario_count == 1
    link = bundle.links[0]
    assert link.method is ResolutionMethod.GROUP_SEGMENT_DECLARATION
    assert link.notes == "MULTI_SCENARIO_DOCUMENT"
    assert set(link.scenario_ids) == {"P8", "P9"}


def test_whitespace_layout_name_routes_level4() -> None:
    doc = make_document(
        artifact="layout",
        raw_text="Letterhead\nEkibastuz Power\nServices JSC\nInternal memo",
    )
    bundle = _route(
        doc,
        accounts=(),
        borrowers=(),
        scenario_accounts={"P5": frozenset({"ACC-7805"})},
        identities={"P5": frozenset({"ekibastuz power services jsc"})},
        raws={"ekibastuz power services jsc": ("Ekibastuz Power Services JSC",)},
    )
    link = bundle.links[0]
    assert link.method is ResolutionMethod.NORMALIZED_LEGAL_NAME
    assert link.scenario_ids == ("P5",)
    assert link.evidence_span_ids
