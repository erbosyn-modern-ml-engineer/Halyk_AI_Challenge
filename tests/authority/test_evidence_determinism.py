"""Evidence and determinism tests for Stage 5C."""

# ruff: noqa: RUF001

from __future__ import annotations

from halyk_agent.domain.authority.engine import run_authority
from halyk_agent.domain.authority.models import DocumentType
from halyk_agent.domain.routing.models import RoutingManifest
from tests.authority.helpers import make_document, make_link


def _manifest() -> RoutingManifest:
    return RoutingManifest(
        dataset_manifest_hash="d" * 64,
        canonical_documents_hash="c" * 64,
        scenario_count=1,
        resolved_document_count=2,
        unresolved_document_count=0,
        transaction_link_count=0,
        conflict_count=0,
        template_cell_count=1,
        ledger_row_count=0,
        scenario_transaction_count=0,
        multi_scenario_document_count=0,
    )


def test_accepted_classification_is_evidence_backed() -> None:
    doc = make_document(raw_text="ИСПОЛНИТЕЛЬНЫЙ ЭКЗЕМПЛЯР\nДОГОВОР БАНКОВСКОГО ЗАЙМА № ACC-7801")
    report = run_authority(
        documents=(doc,),
        document_links=(make_link(doc),),
        routing_manifest=_manifest(),
    )
    item = report.classifications[0]
    assert item.document_type is DocumentType.LOAN_AGREEMENT
    assert item.evidence_span_ids
    assert report.evidence
    assert all(e.source_sha256 for e in report.evidence)
    assert all(e.raw_quote for e in report.evidence)


def test_authority_decision_lists_winners_and_rejected() -> None:
    current = make_document(
        artifact="c",
        sha="b" * 64,
        raw_text="ИСПОЛНИТЕЛЬНЫЙ ЭКЗЕМПЛЯР\nДОГОВОР БАНКОВСКОГО ЗАЙМА № ACC-7801",
    )
    obsolete = make_document(
        artifact="o",
        sha="c" * 64,
        raw_text="НЕДЕЙСТВУЮЩАЯ РЕДАКЦИЯ. НЕ ПРИМЕНЯЕТСЯ.\nДОГОВОР БАНКОВСКОГО ЗАЙМА № ACC-7801",
    )
    report = run_authority(
        documents=(current, obsolete),
        document_links=(make_link(current), make_link(obsolete)),
        routing_manifest=_manifest(),
    )
    decision = report.decisions[0]
    assert decision.winning_document_ids
    assert decision.rejected_document_ids
    assert decision.rule_id
    assert decision.reason


def test_byte_identical_outputs_order_invariant() -> None:
    a = make_document(
        artifact="a",
        sha="1" * 64,
        raw_text="ИСПОЛНИТЕЛЬНЫЙ ЭКЗЕМПЛЯР\nДОГОВОР БАНКОВСКОГО ЗАЙМА № ACC-7801",
    )
    b = make_document(
        artifact="b",
        sha="2" * 64,
        raw_text="Пресс-релиз компании Alpha",
    )
    kwargs = dict(routing_manifest=_manifest())
    r1 = run_authority(
        documents=(a, b),
        document_links=(make_link(a), make_link(b)),
        **kwargs,
    )
    r2 = run_authority(
        documents=(b, a),
        document_links=(make_link(b), make_link(a)),
        **kwargs,
    )
    assert r1.manifest.model_dump_json() == r2.manifest.model_dump_json()
    assert [c.document_id for c in r1.classifications] == [
        c.document_id for c in r2.classifications
    ]
    assert "\n".join(e.model_dump_json() for e in r1.evidence) == "\n".join(
        e.model_dump_json() for e in r2.evidence
    )
