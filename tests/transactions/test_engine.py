"""Stage 5F engine: adjustments, related-party, derived inputs, conflicts."""

from __future__ import annotations

from decimal import Decimal

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
    PeriodDisposition,
    ReclassificationDisposition,
    RelatedPartyThresholdPayload,
    TransactionPeriodPayload,
    TransactionReclassificationPayload,
)
from halyk_agent.domain.routing.models import (
    LedgerRow,
    ResolutionConfidence,
    ResolutionMethod,
    TransactionEntityLink,
)
from halyk_agent.domain.transaction_taxonomy.engine import run_transaction_taxonomy
from halyk_agent.domain.transaction_taxonomy.models import (
    AdjustmentEventType,
    ClassificationStatus,
    InputSourceKind,
    RelatedPartyStatus,
)
from halyk_agent.domain.transaction_taxonomy.related_party import qualifying_related_parties


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
    currency: str = "USD",
    date: str = "2025-06-01",
) -> LedgerRow:
    return LedgerRow(
        row_index=row,
        txn_id=txn,
        date=date,
        account_id="ACC-P1",
        counterparty=counterparty,
        description=description,
        amount=amount,
        currency=currency,
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


def _definition(scenario_id: str = "P1") -> CovenantDefinition:
    selector = TransactionSelector(category=MetricCategory.OPEX)
    return CovenantDefinition(
        definition_id=f"{scenario_id}-1",
        scenario_id=scenario_id,
        clause_id="c1",
        document_id="d1",
        document_version_id="v1",
        source_file="loan.pdf",
        source_sha256="b" * 64,
        family_id="f1",
        metric=TransactionSet(selector=selector),
        metric_quantity_type=QuantityType.MONEY,
        comparator=Comparator.LTE,
        threshold=TypedQuantity(quantity_type=QuantityType.RATIO, value=Decimal("1")),
        period=PeriodDefinition(period_kind=PeriodKind.CLOSED_INTERVAL),
        scope=ScopeDefinition(scope_kind=ScopeKind.BORROWER),
        selectors=(selector,),
        evidence=CovenantEvidenceRefs(),
        rendered="test",
    )


def test_accepted_and_rejected_reclassification() -> None:
    rows = (
        _row("TXN-P1-0001", row=0, amount="-100.00", description="Advisory engagement fee"),
        _row("TXN-P1-0002", row=1, amount="-200.00", description="Fire safety systems servicing"),
    )
    links = (_link("TXN-P1-0001", "P1", 0), _link("TXN-P1-0002", "P1", 1))
    facts = (
        _fact(
            fact_id="f-acc",
            scenario_id="P1",
            kind=FactKind.TRANSACTION_RECLASSIFICATION,
            payload=TransactionReclassificationPayload(
                transaction_id="TXN-P1-0001",
                amount=MoneyAmount(value=Decimal("100.00"), currency="USD"),
                from_category="Operating Expenses",
                to_category="Insurance Premiums",
                disposition=ReclassificationDisposition.ACCEPTED,
            ),
        ),
        _fact(
            fact_id="f-rej",
            scenario_id="P1",
            kind=FactKind.TRANSACTION_RECLASSIFICATION,
            payload=TransactionReclassificationPayload(
                transaction_id="TXN-P1-0002",
                amount=MoneyAmount(value=Decimal("200.00"), currency="USD"),
                from_category="Operating Expenses",
                to_category="Insurance Premiums",
                disposition=ReclassificationDisposition.REJECTED,
            ),
        ),
    )
    report = run_transaction_taxonomy(
        ledger_rows=rows,
        transaction_links=links,
        definitions=(_definition(),),
        accepted_facts=facts,
        ledger_source_sha256="c" * 64,
        routing_manifest_hash="r" * 64,
        covenant_manifest_hash="k" * 64,
        facts_manifest_hash="f" * 64,
    )
    by_id = {t.transaction_id: t for t in report.classified}
    assert by_id["TXN-P1-0001"].original_category is MetricCategory.OTHER_EXPENSE
    assert by_id["TXN-P1-0001"].effective_category is MetricCategory.INSURANCE_PREMIUMS
    assert by_id["TXN-P1-0002"].effective_category == by_id["TXN-P1-0002"].original_category
    assert "f-rej" in by_id["TXN-P1-0002"].rejected_fact_ids
    types = {a.event_type for a in report.adjustments}
    assert AdjustmentEventType.CATEGORY_RECLASSIFICATION_ACCEPTED in types
    assert AdjustmentEventType.CATEGORY_RECLASSIFICATION_REJECTED in types


def test_amount_correction_single_input_no_double_count() -> None:
    rows = (_row("TXN-P1-0003", row=0, amount="", description="Mineral extraction tax assessment"),)
    links = (_link("TXN-P1-0003", "P1", 0),)
    facts = (
        _fact(
            fact_id="f-amt",
            scenario_id="P1",
            kind=FactKind.AMOUNT_CORRECTION,
            payload=AmountCorrectionPayload(
                transaction_id="TXN-P1-0003",
                amount=MoneyAmount(value=Decimal("120.00"), currency="USD"),
            ),
        ),
    )
    report = run_transaction_taxonomy(
        ledger_rows=rows,
        transaction_links=links,
        definitions=(_definition(),),
        accepted_facts=facts,
        ledger_source_sha256="c" * 64,
        routing_manifest_hash="r" * 64,
        covenant_manifest_hash="k" * 64,
        facts_manifest_hash="f" * 64,
    )
    inputs = [i for i in report.calculation_inputs if i.transaction_id == "TXN-P1-0003"]
    assert len(inputs) == 1
    # Source signed flow preserved; metric_amount uses expense negate contract.
    assert inputs[0].source_amount == Decimal("-120.00")
    assert inputs[0].amount == Decimal("120.00")
    assert inputs[0].metric_amount == Decimal("120.00")


def test_conflicting_amount_corrections() -> None:
    rows = (
        _row("TXN-P1-0004", row=0, amount="-50.00", description="Corporate income tax instalment"),
    )
    links = (_link("TXN-P1-0004", "P1", 0),)
    facts = (
        _fact(
            fact_id="f-a1",
            scenario_id="P1",
            kind=FactKind.AMOUNT_CORRECTION,
            payload=AmountCorrectionPayload(
                transaction_id="TXN-P1-0004",
                amount=MoneyAmount(value=Decimal("50.00"), currency="USD"),
            ),
        ),
        _fact(
            fact_id="f-a2",
            scenario_id="P1",
            kind=FactKind.AMOUNT_CORRECTION,
            payload=AmountCorrectionPayload(
                transaction_id="TXN-P1-0004",
                amount=MoneyAmount(value=Decimal("80.00"), currency="USD"),
            ),
        ),
    )
    report = run_transaction_taxonomy(
        ledger_rows=rows,
        transaction_links=links,
        definitions=(_definition(),),
        accepted_facts=facts,
        ledger_source_sha256="c" * 64,
        routing_manifest_hash="r" * 64,
        covenant_manifest_hash="k" * 64,
        facts_manifest_hash="f" * 64,
    )
    assert any(c.kind.value == "AMOUNT_CORRECTION_CONFLICT" for c in report.conflicts)


def test_period_assignment_and_exclusion() -> None:
    rows = (
        _row("TXN-P1-0010", row=0, amount="-10.00", description="Advisory retainer"),
        _row("TXN-P1-0011", row=1, amount="-11.00", description="Advisory retainer Q2"),
    )
    links = (_link("TXN-P1-0010", "P1", 0), _link("TXN-P1-0011", "P1", 1))
    facts = (
        _fact(
            fact_id="f-ex",
            scenario_id="P1",
            kind=FactKind.TRANSACTION_PERIOD,
            payload=TransactionPeriodPayload(
                transaction_id="TXN-P1-0010",
                disposition=PeriodDisposition.EXCLUDE_FROM_PERIOD,
                period_label="2025",
            ),
        ),
        _fact(
            fact_id="f-as",
            scenario_id="P1",
            kind=FactKind.TRANSACTION_PERIOD,
            payload=TransactionPeriodPayload(
                transaction_id="TXN-P1-0011",
                disposition=PeriodDisposition.ASSIGN_TO_PERIOD,
                period_label="svc",
                service_start=__import__("datetime").date(2026, 1, 15),
                service_end=__import__("datetime").date(2026, 3, 20),
            ),
        ),
    )
    report = run_transaction_taxonomy(
        ledger_rows=rows,
        transaction_links=links,
        definitions=(_definition(),),
        accepted_facts=facts,
        ledger_source_sha256="c" * 64,
        routing_manifest_hash="r" * 64,
        covenant_manifest_hash="k" * 64,
        facts_manifest_hash="f" * 64,
    )
    by_id = {t.transaction_id: t for t in report.classified}
    assert by_id["TXN-P1-0010"].period_excluded is True
    assert by_id["TXN-P1-0011"].effective_period_start.isoformat() == "2026-01-15"


def test_related_party_threshold_boundary_and_legal_form() -> None:
    facts = (
        _fact(
            fact_id="thr",
            scenario_id="P1",
            kind=FactKind.RELATED_PARTY_THRESHOLD,
            payload=RelatedPartyThresholdPayload(threshold_percent=Decimal("25.0")),
        ),
        _fact(
            fact_id="own-eq",
            scenario_id="P1",
            kind=FactKind.OWNERSHIP,
            payload=OwnershipPayload(
                entity_name="Aktau Holdings LLP", ownership_percent=Decimal("25.0")
            ),
        ),
        _fact(
            fact_id="own-jsc",
            scenario_id="P1",
            kind=FactKind.OWNERSHIP,
            payload=OwnershipPayload(
                entity_name="Aktau Holdings JSC", ownership_percent=Decimal("40.0")
            ),
        ),
    )
    quals = qualifying_related_parties(facts)
    names = {q.entity_name for q in quals}
    assert "Aktau Holdings LLP" in names  # exact threshold >= qualifies
    assert "Aktau Holdings JSC" in names

    rows = (
        _row(
            "TXN-P1-0020",
            row=0,
            amount="-1.00",
            description="Advisory retainer",
            counterparty="Aktau Holdings LLP",
        ),
        _row(
            "TXN-P1-0021",
            row=1,
            amount="-1.00",
            description="Advisory retainer",
            counterparty="Aktau Holdings JSC",
        ),
        _row(
            "TXN-P1-0022",
            row=2,
            amount="-1.00",
            description="Advisory retainer",
            counterparty="Unknown Counterparty LLP",
        ),
    )
    links = tuple(_link(r.txn_id, "P1", r.row_index) for r in rows)
    report = run_transaction_taxonomy(
        ledger_rows=rows,
        transaction_links=links,
        definitions=(_definition(),),
        accepted_facts=facts,
        ledger_source_sha256="c" * 64,
        routing_manifest_hash="r" * 64,
        covenant_manifest_hash="k" * 64,
        facts_manifest_hash="f" * 64,
    )
    by_id = {t.transaction_id: t for t in report.classified}
    assert by_id["TXN-P1-0020"].related_party_status is RelatedPartyStatus.TRUE
    # JSC != LLP: payment to JSC entity that qualifies on its own name
    assert by_id["TXN-P1-0021"].related_party_status is RelatedPartyStatus.TRUE
    assert by_id["TXN-P1-0022"].related_party_status is RelatedPartyStatus.FALSE


def test_off_ledger_derived_not_duplicated() -> None:
    rows = (_row("TXN-P1-0030", row=0, amount="-5.00", description="Advisory retainer"),)
    links = (_link("TXN-P1-0030", "P1", 0),)
    facts = (
        _fact(
            fact_id="f-off",
            scenario_id="P1",
            kind=FactKind.OFF_LEDGER_AMOUNT,
            payload=OffLedgerAmountPayload(
                label="severance_liability",
                amount=MoneyAmount(value=Decimal("918447.52"), currency="USD"),
            ),
        ),
    )
    report = run_transaction_taxonomy(
        ledger_rows=rows,
        transaction_links=links,
        definitions=(_definition(),),
        accepted_facts=facts,
        ledger_source_sha256="c" * 64,
        routing_manifest_hash="r" * 64,
        covenant_manifest_hash="k" * 64,
        facts_manifest_hash="f" * 64,
    )
    assert len(report.derived_inputs) == 1
    derived_calc = [
        i for i in report.calculation_inputs if i.source_kind is InputSourceKind.AUTHORITATIVE_FACT
    ]
    assert len(derived_calc) == 1
    assert derived_calc[0].flags == ("OFF_LEDGER",)


def test_noise_rows_excluded_from_calculation_inputs() -> None:
    rows = (
        _row("TXN-P1-0040", row=0, amount="-1.00", description="Advisory retainer"),
        _row("TXN-9001-0001", row=1, amount="-1.00", description="Advisory retainer"),
    )
    links = (
        _link("TXN-P1-0040", "P1", 0),
        TransactionEntityLink(
            txn_id="TXN-9001-0001",
            row_index=1,
            ledger_source_file="ledger.csv",
            account_id_raw="ACC-X",
            account_id_normalized="ACC-X",
            scenario_id=None,
            scenario_token="9001",
            method=ResolutionMethod.UNRESOLVED,
            confidence=ResolutionConfidence.UNRESOLVED,
            counterparty_raw="noise-counterparty",
        ),
    )
    report = run_transaction_taxonomy(
        ledger_rows=rows,
        transaction_links=links,
        definitions=(_definition(),),
        accepted_facts=(),
        ledger_source_sha256="c" * 64,
        routing_manifest_hash="r" * 64,
        covenant_manifest_hash="k" * 64,
        facts_manifest_hash="f" * 64,
    )
    assert report.manifest.routing_noise_count == 1
    assert all(i.transaction_id != "TXN-9001-0001" for i in report.calculation_inputs)
    noise = next(t for t in report.classified if t.transaction_id == "TXN-9001-0001")
    assert noise.classification_status is ClassificationStatus.ROUTING_NOISE


def test_determinism_shuffle_invariant() -> None:
    rows = (
        _row("TXN-P1-0050", row=0, amount="-10.00", description="Payroll for laboratory staff"),
        _row("TXN-P1-0051", row=1, amount="-20.00", description="Warehouse lease — June"),
    )
    links = (_link("TXN-P1-0050", "P1", 0), _link("TXN-P1-0051", "P1", 1))
    args = dict(
        definitions=(_definition(),),
        accepted_facts=(),
        ledger_source_sha256="c" * 64,
        routing_manifest_hash="r" * 64,
        covenant_manifest_hash="k" * 64,
        facts_manifest_hash="f" * 64,
    )
    r1 = run_transaction_taxonomy(ledger_rows=rows, transaction_links=links, **args)
    r2 = run_transaction_taxonomy(
        ledger_rows=tuple(reversed(rows)),
        transaction_links=tuple(reversed(links)),
        **args,
    )
    assert r1.manifest.taxonomy_hash == r2.manifest.taxonomy_hash
    assert r1.manifest.calculation_inputs_hash == r2.manifest.calculation_inputs_hash
