"""Stage 5F.1 semantic correctness regressions (memberships, revenue, RP, scope)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from halyk_agent.domain.authority.models import AuthorityDomain
from halyk_agent.domain.covenants.ast import MetricCategory, TransactionSelector, TransactionSet
from halyk_agent.domain.covenants.models import (
    Comparator,
    CovenantDefinition,
    CovenantEvidenceRefs,
    PeriodDefinition,
    PeriodKind,
    ScopeDefinition,
    ScopeKind,
)
from halyk_agent.domain.covenants.quantity import QuantityType, TypedQuantity
from halyk_agent.domain.fact_extraction.models import (
    AmountCorrectionPayload,
    ExtractionMethod,
    FactKind,
    FactRecord,
    FactValidatorStatus,
    MoneyAmount,
    OffLedgerAmountPayload,
    OwnershipPayload,
    ReclassificationDisposition,
    RelatedPartyThresholdPayload,
    SubsidiaryKind,
    SubsidiaryStatusPayload,
    TransactionReclassificationPayload,
)
from halyk_agent.domain.routing.models import (
    LedgerRow,
    ResolutionConfidence,
    ResolutionMethod,
    TransactionEntityLink,
)
from halyk_agent.domain.transaction_taxonomy.classify import classify_description
from halyk_agent.domain.transaction_taxonomy.engine import run_transaction_taxonomy
from halyk_agent.domain.transaction_taxonomy.membership import selector_memberships
from halyk_agent.domain.transaction_taxonomy.models import (
    AdjustmentEventType,
    InputSourceKind,
    RelatedPartyStatus,
    SelectorReadinessStatus,
    SubsidiaryStatusKind,
)
from halyk_agent.domain.transaction_taxonomy.selectors import input_matches_selector


def _link(txn: str, scenario: str, row: int = 0) -> TransactionEntityLink:
    return TransactionEntityLink(
        txn_id=txn,
        row_index=row,
        ledger_source_file="ledger.csv",
        account_id_raw=f"ACC-{scenario}",
        account_id_normalized=f"ACC-{scenario}",
        scenario_id=scenario,
        scenario_token=scenario,
        method=ResolutionMethod.TXN_ID_PREFIX,
        confidence=ResolutionConfidence.EXACT,
        counterparty_raw="link-counterparty",
    )


def _row(
    txn: str,
    *,
    row: int,
    amount: str,
    description: str,
    counterparty: str = "Neutral Vendor LLP",
) -> LedgerRow:
    return LedgerRow(
        row_index=row,
        txn_id=txn,
        date="2025-06-01",
        account_id="ACC-P1",
        counterparty=counterparty,
        description=description,
        amount=amount,
        currency="USD",
        ledger_source_file="ledger.csv",
    )


def _fact(
    *,
    fact_id: str,
    scenario_id: str,
    kind: FactKind,
    payload: object,
) -> FactRecord:
    return FactRecord(
        fact_id=fact_id,
        scenario_id=scenario_id,
        fact_kind=kind,
        payload=payload,  # type: ignore[arg-type]
        authority_domain=AuthorityDomain.FINANCIAL_ADJUSTMENTS,
        source_document_id="doc-1",
        source_file="note.pdf",
        source_sha256="a" * 64,
        evidence_span_ids=("span-1",),
        extraction_method=ExtractionMethod.DETERMINISTIC,
        validator_status=FactValidatorStatus.ACCEPTED,
        requirement_ids=("req-1",),
        reason_code="TEST",
    )


def _definition(
    scenario_id: str,
    *categories: MetricCategory,
    related_party_only: bool = False,
    group_level: bool = False,
    definition_id: str | None = None,
) -> CovenantDefinition:
    selectors = tuple(
        TransactionSelector(
            category=cat,
            related_party_only=related_party_only,
            group_level=group_level,
        )
        for cat in categories
    )
    primary = selectors[0]
    return CovenantDefinition(
        definition_id=definition_id or f"{scenario_id}-1",
        scenario_id=scenario_id,
        clause_id="c1",
        document_id="d1",
        document_version_id="v1",
        source_file="loan.pdf",
        source_sha256="b" * 64,
        family_id="f1",
        metric=TransactionSet(selector=primary),
        metric_quantity_type=QuantityType.MONEY,
        comparator=Comparator.LTE,
        threshold=TypedQuantity(quantity_type=QuantityType.RATIO, value=Decimal("1")),
        period=PeriodDefinition(period_kind=PeriodKind.CLOSED_INTERVAL),
        scope=ScopeDefinition(scope_kind=ScopeKind.BORROWER),
        selectors=selectors,
        evidence=CovenantEvidenceRefs(),
        rendered="test",
    )


def _run(
    rows: tuple[LedgerRow, ...],
    links: tuple[TransactionEntityLink, ...],
    definitions: tuple[CovenantDefinition, ...],
    facts: tuple[FactRecord, ...] = (),
    fact_requirement_results=None,
):
    return run_transaction_taxonomy(
        ledger_rows=rows,
        transaction_links=links,
        definitions=definitions,
        accepted_facts=facts,
        ledger_source_sha256="c" * 64,
        routing_manifest_hash="r" * 64,
        covenant_manifest_hash="k" * 64,
        facts_manifest_hash="f" * 64,
        fact_requirement_results=fact_requirement_results,
    )


def test_statement_opex_does_not_widen_specialized_expense_families() -> None:
    rows = (
        _row("TXN-X-1", row=0, amount="-100.00", description="Plant insurance premium 2025"),
        _row("TXN-X-2", row=1, amount="-50.00", description="Payroll for laboratory staff"),
        _row("TXN-X-3", row=2, amount="-75.00", description="Warehouse lease — June"),
        _row("TXN-X-4", row=3, amount="-200.00", description="Operating expenses — June"),
    )
    links = tuple(_link(r.txn_id, "P1", r.row_index) for r in rows)
    report = _run(
        rows,
        links,
        (_definition("P1", MetricCategory.OPEX, MetricCategory.LEASE_PAYMENTS),),
    )
    by_txn = {i.transaction_id: i for i in report.calculation_inputs}

    ins = by_txn["TXN-X-1"]
    assert ins.category is MetricCategory.INSURANCE_PREMIUMS
    assert MetricCategory.OPEX not in ins.selector_categories

    labor = by_txn["TXN-X-2"]
    assert labor.category is MetricCategory.LABOR
    assert MetricCategory.OPEX not in labor.selector_categories

    lease = by_txn["TXN-X-3"]
    assert lease.category is MetricCategory.LEASE_PAYMENTS
    assert MetricCategory.RENT in lease.selector_categories
    assert MetricCategory.OPEX not in lease.selector_categories

    opex = by_txn["TXN-X-4"]
    assert opex.category is MetricCategory.OPEX
    assert MetricCategory.OPEX in opex.selector_categories

    opex_sel = TransactionSelector(category=MetricCategory.OPEX)
    lease_sel = TransactionSelector(category=MetricCategory.LEASE_PAYMENTS)
    assert not input_matches_selector(ins, opex_sel)
    assert not input_matches_selector(labor, opex_sel)
    assert input_matches_selector(lease, lease_sel)
    assert not input_matches_selector(lease, opex_sel)
    assert input_matches_selector(opex, opex_sel)


def test_membership_helpers_preserve_statement_line_boundaries() -> None:
    for primary in (
        MetricCategory.LABOR,
        MetricCategory.UTILITIES,
        MetricCategory.INSURANCE_PREMIUMS,
        MetricCategory.RENT,
        MetricCategory.TAXES,
    ):
        members = selector_memberships(primary, description="Property tax assessment")
        assert members[0] is primary
        assert MetricCategory.OPEX not in members

    property_lease = selector_memberships(
        MetricCategory.LEASE_PAYMENTS, description="Warehouse lease — June"
    )
    assert MetricCategory.RENT in property_lease
    telecom_lease = selector_memberships(
        MetricCategory.LEASE_PAYMENTS, description="Telecom leased line — site office"
    )
    assert MetricCategory.RENT not in telecom_lease

    for primary in (
        MetricCategory.CAPITAL_ASSET_TRANSFER,
        MetricCategory.CAPITAL_ASSET_TRANSFERS_TO_UNRESTRICTED_SUBS,
    ):
        assert MetricCategory.CAPEX in selector_memberships(primary)

    assert selector_memberships(MetricCategory.OPEX) == (MetricCategory.OPEX,)
    assert MetricCategory.OPEX not in selector_memberships(MetricCategory.CAPEX)
    assert MetricCategory.OPEX not in selector_memberships(MetricCategory.REVENUE)


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("Utility deposit returned — site office", MetricCategory.UTILITIES),
        ("VAT refund received — Q1", MetricCategory.TAXES),
        ("Insurance broker rebate — annual", MetricCategory.INSURANCE_PREMIUMS),
        ("Tax overpayment refunded", MetricCategory.TAXES),
        ("Marketing overbilling refund", MetricCategory.OTHER_EXPENSE),
        ("Interest income on treasury bills", MetricCategory.NON_OPERATING_INCOME),
        ("Interest credited on current account", MetricCategory.NON_OPERATING_INCOME),
        ("Customer invoice collection — April", MetricCategory.REVENUE),
        ("Sales revenue settlement — export lot", MetricCategory.REVENUE),
    ],
)
def test_revenue_vs_refund_and_genuine_revenue(text: str, category: MetricCategory) -> None:
    hit = classify_description(text)
    assert hit.status == "CLASSIFIED"
    assert hit.category is category
    if category is not MetricCategory.REVENUE:
        assert hit.category is not MetricCategory.REVENUE


def test_llp_punctuation_related_party_true() -> None:
    facts = (
        _fact(
            fact_id="thr",
            scenario_id="P1",
            kind=FactKind.RELATED_PARTY_THRESHOLD,
            payload=RelatedPartyThresholdPayload(threshold_percent=Decimal("25.0")),
        ),
        _fact(
            fact_id="own",
            scenario_id="P1",
            kind=FactKind.OWNERSHIP,
            payload=OwnershipPayload(
                entity_name="Kazyna Capital LLP", ownership_percent=Decimal("30.0")
            ),
        ),
    )
    rows = (
        _row(
            "garbage",
            row=0,
            amount="-10.00",
            description="Advisory retainer",
            counterparty="Kazyna Capital LLP.",
        ),
        _row(
            "TXN-P1-90",
            row=1,
            amount="-11.00",
            description="Advisory retainer",
            counterparty="Kazyna Capital L.L.P.",
        ),
    )
    # Malformed txn id still uses Stage 5B routing scenario ownership.
    links = (_link("garbage", "P1", 0), _link("TXN-P1-90", "P1", 1))
    report = _run(rows, links, (_definition("P1", MetricCategory.OPEX),), facts)
    by_id = {t.transaction_id: t for t in report.classified}
    assert by_id["garbage"].scenario_id == "P1"
    assert by_id["garbage"].related_party_status is RelatedPartyStatus.TRUE
    assert by_id["TXN-P1-90"].related_party_status is RelatedPartyStatus.TRUE


def test_jsc_not_llp_and_services_suffix_distinct() -> None:
    facts = (
        _fact(
            fact_id="thr",
            scenario_id="P1",
            kind=FactKind.RELATED_PARTY_THRESHOLD,
            payload=RelatedPartyThresholdPayload(threshold_percent=Decimal("25.0")),
        ),
        _fact(
            fact_id="own",
            scenario_id="P1",
            kind=FactKind.OWNERSHIP,
            payload=OwnershipPayload(
                entity_name="Alpha Energy JSC", ownership_percent=Decimal("40.0")
            ),
        ),
    )
    rows = (
        _row(
            "TXN-P1-a",
            row=0,
            amount="-1.00",
            description="Advisory retainer",
            counterparty="Alpha Energy LLP",
        ),
        _row(
            "TXN-P1-b",
            row=1,
            amount="-1.00",
            description="Advisory retainer",
            counterparty="Alpha Energy Services JSC",
        ),
        _row(
            "TXN-P1-c",
            row=2,
            amount="-1.00",
            description="Advisory retainer",
            counterparty="Alpha Energy JSC",
        ),
    )
    links = tuple(_link(r.txn_id, "P1", r.row_index) for r in rows)
    report = _run(rows, links, (_definition("P1", MetricCategory.OPEX),), facts)
    by_id = {t.transaction_id: t for t in report.classified}
    assert by_id["TXN-P1-a"].related_party_status is RelatedPartyStatus.FALSE
    assert by_id["TXN-P1-b"].related_party_status is RelatedPartyStatus.FALSE
    assert by_id["TXN-P1-c"].related_party_status is RelatedPartyStatus.TRUE


def test_unique_corrupted_ownership_identity_is_recovered() -> None:
    facts = (
        _fact(
            fact_id="thr",
            scenario_id="P6",
            kind=FactKind.RELATED_PARTY_THRESHOLD,
            payload=RelatedPartyThresholdPayload(threshold_percent=Decimal("25.0")),
        ),
        _fact(
            fact_id="own-damaged",
            scenario_id="P6",
            kind=FactKind.OWNERSHIP,
            payload=OwnershipPayload(
                entity_name="Таға? Holding Group LLP", ownership_percent=Decimal("40.0")
            ),
        ),
        _fact(
            fact_id="own-ok",
            scenario_id="P6",
            kind=FactKind.OWNERSHIP,
            payload=OwnershipPayload(
                entity_name="Other Holdings LLP", ownership_percent=Decimal("40.0")
            ),
        ),
    )
    rows = (
        _row(
            "TXN-P6-1",
            row=0,
            amount="-1.00",
            description="Advisory retainer",
            counterparty="Taraz Holding Group LLP",
        ),
        _row(
            "TXN-P6-2",
            row=1,
            amount="-1.00",
            description="Advisory retainer",
            counterparty="Other Holdings LLP",
        ),
    )
    links = tuple(_link(r.txn_id, "P6", r.row_index) for r in rows)
    report = _run(rows, links, (_definition("P6", MetricCategory.OPEX),), facts)
    by_id = {t.transaction_id: t for t in report.classified}
    assert by_id["TXN-P6-1"].related_party_status is RelatedPartyStatus.TRUE
    assert by_id["TXN-P6-2"].related_party_status is RelatedPartyStatus.TRUE


def test_corrupted_ownership_identity_stays_unknown_when_ledger_match_is_ambiguous() -> None:
    facts = (
        _fact(
            fact_id="thr",
            scenario_id="P6",
            kind=FactKind.RELATED_PARTY_THRESHOLD,
            payload=RelatedPartyThresholdPayload(threshold_percent=Decimal("25.0")),
        ),
        _fact(
            fact_id="own-damaged",
            scenario_id="P6",
            kind=FactKind.OWNERSHIP,
            payload=OwnershipPayload(
                entity_name="Таға? Holding Group LLP", ownership_percent=Decimal("40.0")
            ),
        ),
    )
    rows = (
        _row(
            "TXN-P6-1",
            row=0,
            amount="-1.00",
            description="Advisory retainer",
            counterparty="Taraz Holding Group LLP",
        ),
        _row(
            "TXN-P6-2",
            row=1,
            amount="-1.00",
            description="Advisory retainer",
            counterparty="Tulpar Holding Group LLP",
        ),
    )
    links = tuple(_link(r.txn_id, "P6", r.row_index) for r in rows)
    report = _run(rows, links, (_definition("P6", MetricCategory.OPEX),), facts)
    by_id = {t.transaction_id: t for t in report.classified}
    assert by_id["TXN-P6-1"].related_party_status is RelatedPartyStatus.UNKNOWN
    assert by_id["TXN-P6-2"].related_party_status is RelatedPartyStatus.UNKNOWN


def test_unrestricted_subsidiary_requires_evidence() -> None:
    rows = (
        _row(
            "TXN-P9-1",
            row=0,
            amount="-1000.00",
            description="Transfer of equipment to subsidiary",
        ),
    )
    links = (_link("TXN-P9-1", "P9", 0),)
    defs = (
        _definition(
            "P9",
            MetricCategory.CAPITAL_ASSET_TRANSFERS_TO_UNRESTRICTED_SUBS,
            definition_id="P9-unres",
        ),
    )
    report = _run(rows, links, defs)
    inp = report.calculation_inputs[0]
    assert inp.category is MetricCategory.CAPITAL_ASSET_TRANSFER
    assert inp.subsidiary_status is SubsidiaryStatusKind.UNKNOWN
    sel = TransactionSelector(category=MetricCategory.CAPITAL_ASSET_TRANSFERS_TO_UNRESTRICTED_SUBS)
    assert not input_matches_selector(inp, sel)
    cov = report.selector_coverage[0]
    assert cov.status is SelectorReadinessStatus.UNRESOLVED
    assert cov.reason_code == "UNRESTRICTED_SUBSIDIARY_STATUS_UNKNOWN"


def test_restricted_and_unrestricted_status_facts() -> None:
    rows = (
        _row(
            "TXN-P9-2",
            row=0,
            amount="-100.00",
            description="Transfer of equipment to subsidiary",
            counterparty="Sub Alpha LLP",
        ),
        _row(
            "TXN-P9-3",
            row=1,
            amount="-200.00",
            description="Transfer of equipment to subsidiary",
            counterparty="Sub Beta LLP",
        ),
    )
    links = (_link("TXN-P9-2", "P9", 0), _link("TXN-P9-3", "P9", 1))
    facts = (
        _fact(
            fact_id="s-un",
            scenario_id="P9",
            kind=FactKind.SUBSIDIARY_STATUS,
            payload=SubsidiaryStatusPayload(
                entity_name="Sub Alpha LLP", status=SubsidiaryKind.UNRESTRICTED
            ),
        ),
        _fact(
            fact_id="s-re",
            scenario_id="P9",
            kind=FactKind.SUBSIDIARY_STATUS,
            payload=SubsidiaryStatusPayload(
                entity_name="Sub Beta LLP", status=SubsidiaryKind.RESTRICTED
            ),
        ),
    )
    defs = (
        _definition(
            "P9",
            MetricCategory.CAPITAL_ASSET_TRANSFERS_TO_UNRESTRICTED_SUBS,
        ),
    )
    report = _run(rows, links, defs, facts)
    by_txn = {i.transaction_id: i for i in report.calculation_inputs}
    sel = TransactionSelector(category=MetricCategory.CAPITAL_ASSET_TRANSFERS_TO_UNRESTRICTED_SUBS)
    assert by_txn["TXN-P9-2"].subsidiary_status is SubsidiaryStatusKind.UNRESTRICTED
    assert by_txn["TXN-P9-2"].category is (
        MetricCategory.CAPITAL_ASSET_TRANSFERS_TO_UNRESTRICTED_SUBS
    )
    assert input_matches_selector(by_txn["TXN-P9-2"], sel)
    assert by_txn["TXN-P9-3"].subsidiary_status is SubsidiaryStatusKind.RESTRICTED
    assert not input_matches_selector(by_txn["TXN-P9-3"], sel)


def test_group_capex_does_not_consume_borrower_capex() -> None:
    rows = (_row("TXN-P5-1", row=0, amount="-500.00", description="Equipment purchase — crusher"),)
    links = (_link("TXN-P5-1", "P5", 0),)
    defs = (_definition("P5", MetricCategory.GROUP_CAPEX, MetricCategory.CAPEX),)
    report = _run(rows, links, defs)
    inp = next(i for i in report.calculation_inputs if i.transaction_id == "TXN-P5-1")
    assert inp.category is MetricCategory.CAPEX
    assert "GROUP_SCOPE" not in inp.flags
    assert "GROUP_LEVEL_SOURCE" not in inp.flags
    group_sel = TransactionSelector(category=MetricCategory.GROUP_CAPEX)
    borrower_sel = TransactionSelector(category=MetricCategory.CAPEX)
    assert not input_matches_selector(inp, group_sel)
    assert input_matches_selector(inp, borrower_sel)
    group_cov = next(
        c for c in report.selector_coverage if c.category is MetricCategory.GROUP_CAPEX
    )
    assert group_cov.status is SelectorReadinessStatus.UNRESOLVED
    assert group_cov.reason_code == "GROUP_CAPEX_OPERAND_UNRESOLVED"
    readiness = report.definition_readiness[0]
    assert readiness.status is SelectorReadinessStatus.UNRESOLVED


def test_true_zero_vs_unresolved_selector() -> None:
    rows = (_row("TXN-P10-1", row=0, amount="-10.00", description="Advisory retainer"),)
    links = (_link("TXN-P10-1", "P10", 0),)
    defs = (
        _definition("P10", MetricCategory.RENT, definition_id="P10-rent"),
        _definition("P10", MetricCategory.GROUP_CAPEX, definition_id="P10-gc"),
        _definition("P10", MetricCategory.ONE_TIME_ADD_BACKS, definition_id="P10-ot"),
    )
    report = _run(rows, links, defs)
    rent = next(c for c in report.selector_coverage if c.definition_id == "P10-rent")
    gc = next(c for c in report.selector_coverage if c.definition_id == "P10-gc")
    ot = next(c for c in report.selector_coverage if c.definition_id == "P10-ot")
    assert rent.status is SelectorReadinessStatus.TRUE_ZERO
    assert gc.status is SelectorReadinessStatus.UNRESOLVED
    # Source-dependent ONE_TIME must not become TRUE_ZERO from empty/incomplete source.
    assert ot.status is SelectorReadinessStatus.UNRESOLVED
    assert ot.reason_code == "UNRESOLVED_SOURCE_QUALITY"


def test_accepted_reclass_survives_memberships() -> None:
    rows = (_row("TXN-P1-r", row=0, amount="-100.00", description="Advisory engagement fee"),)
    links = (_link("TXN-P1-r", "P1", 0),)
    facts = (
        _fact(
            fact_id="f-acc",
            scenario_id="P1",
            kind=FactKind.TRANSACTION_RECLASSIFICATION,
            payload=TransactionReclassificationPayload(
                transaction_id="TXN-P1-r",
                amount=MoneyAmount(value=Decimal("100.00"), currency="USD"),
                from_category="Operating Expenses",
                to_category="Insurance Premiums",
                disposition=ReclassificationDisposition.ACCEPTED,
            ),
        ),
    )
    report = _run(rows, links, (_definition("P1", MetricCategory.OPEX),), facts)
    inp = report.calculation_inputs[0]
    assert inp.category is MetricCategory.INSURANCE_PREMIUMS
    assert MetricCategory.OPEX not in inp.selector_categories
    assert not input_matches_selector(inp, TransactionSelector(category=MetricCategory.OPEX))
    assert inp.source_amount == Decimal("-100.00")
    assert inp.metric_amount == Decimal("100.00")
    assert any(
        a.event_type is AdjustmentEventType.CATEGORY_RECLASSIFICATION_ACCEPTED
        for a in report.adjustments
    )


def test_rejected_reclass_not_applied() -> None:
    rows = (
        _row("TXN-P1-rj", row=0, amount="-200.00", description="Fire safety systems servicing"),
    )
    links = (_link("TXN-P1-rj", "P1", 0),)
    facts = (
        _fact(
            fact_id="f-rej",
            scenario_id="P1",
            kind=FactKind.TRANSACTION_RECLASSIFICATION,
            payload=TransactionReclassificationPayload(
                transaction_id="TXN-P1-rj",
                amount=MoneyAmount(value=Decimal("200.00"), currency="USD"),
                from_category="Operating Expenses",
                to_category="Insurance Premiums",
                disposition=ReclassificationDisposition.REJECTED,
            ),
        ),
    )
    report = _run(rows, links, (_definition("P1", MetricCategory.OPEX),), facts)
    classified = report.classified[0]
    assert classified.effective_category == classified.original_category
    assert "f-rej" in classified.rejected_fact_ids


def test_amount_correction_and_off_ledger_no_duplicate() -> None:
    rows = (_row("TXN-P1-am", row=0, amount="", description="Corporate income tax assessment"),)
    links = (_link("TXN-P1-am", "P1", 0),)
    facts = (
        _fact(
            fact_id="f-amt",
            scenario_id="P1",
            kind=FactKind.AMOUNT_CORRECTION,
            payload=AmountCorrectionPayload(
                transaction_id="TXN-P1-am",
                amount=MoneyAmount(value=Decimal("120.00"), currency="USD"),
            ),
        ),
        _fact(
            fact_id="f-off",
            scenario_id="P1",
            kind=FactKind.OFF_LEDGER_AMOUNT,
            payload=OffLedgerAmountPayload(
                label="severance_liability",
                amount=MoneyAmount(value=Decimal("50.00"), currency="USD"),
            ),
        ),
    )
    report = _run(rows, links, (_definition("P1", MetricCategory.TAXES),), facts)
    ledger_inputs = [i for i in report.calculation_inputs if i.transaction_id == "TXN-P1-am"]
    assert len(ledger_inputs) == 1
    assert ledger_inputs[0].source_amount == Decimal("-120.00")
    assert ledger_inputs[0].amount == Decimal("120.00")
    derived = [
        i for i in report.calculation_inputs if i.source_kind is InputSourceKind.AUTHORITATIVE_FACT
    ]
    assert len(derived) == 1
    assert len(report.derived_inputs) == 1


def test_scenario_universe_mismatch_fails_closed(tmp_path) -> None:
    from pathlib import Path

    from halyk_agent.app.transactions import TransactionServiceError, transactions_from_paths

    # Missing covenants directory must fail closed before trusted publication.
    with pytest.raises(TransactionServiceError) as exc:
        transactions_from_paths(
            routing_dir=Path("work/smoke5b2/routing"),
            covenants_dir=tmp_path / "missing-covenants",
            facts_dir=Path("work/smoke5e3/facts"),
            ledger_path=Path("agentic-bank-public/master_ledger_2025.csv"),
            output_dir=tmp_path / "out",
        )
    assert exc.value.code in {"MISSING_INPUT", "SCENARIO_UNIVERSE_MISMATCH"}


def test_facts_covenants_authority_hash_mismatch_fails_closed(tmp_path) -> None:
    import json
    import shutil
    from pathlib import Path

    from halyk_agent.app.transactions import TransactionServiceError, transactions_from_paths

    smoke_root = Path("work/smoke5f2")
    if not (smoke_root / "routing" / "routing_manifest.json").exists():
        pytest.skip("smoke5f2 artifacts required")

    out = tmp_path / "bundle"
    for name in ("routing", "covenants", "facts"):
        shutil.copytree(smoke_root / name, out / name)
    facts_manifest = out / "facts" / "fact_extraction_manifest.json"
    data = json.loads(facts_manifest.read_text(encoding="utf-8"))
    data["authority_manifest_hash"] = "0" * 64
    facts_manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(TransactionServiceError) as exc:
        transactions_from_paths(
            routing_dir=out / "routing",
            covenants_dir=out / "covenants",
            facts_dir=out / "facts",
            ledger_path=Path("agentic-bank-public/master_ledger_2025.csv"),
            output_dir=tmp_path / "tx-out",
            overwrite=True,
        )
    assert exc.value.code == "AUTHORITY_MISMATCH"
