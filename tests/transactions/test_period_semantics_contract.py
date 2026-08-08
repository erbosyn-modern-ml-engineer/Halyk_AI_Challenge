"""InputPeriodSemantics contract for Stage 6 pre-flight."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from halyk_agent.domain.covenants.ast import MetricCategory
from halyk_agent.domain.covenants.models import PeriodDefinition, PeriodKind
from halyk_agent.domain.fact_extraction.models import (
    FactKind,
    MoneyAmount,
    OffLedgerAmountPayload,
    OneTimeAddBackPayload,
)
from halyk_agent.domain.transaction_taxonomy.models import InputPeriodSemantics
from tests.transactions.test_semantic_correctness import (
    _definition,
    _fact,
    _link,
    _row,
    _run,
)


def test_ledger_rows_are_flow() -> None:
    rows = (_row("TXN-1", row=0, amount="-10.00", description="Office rent payment"),)
    links = (_link("TXN-1", "P1", 0),)
    defs = (_definition("P1", MetricCategory.RENT),)
    report = _run(rows, links, defs)
    assert len(report.calculation_inputs) == 1
    assert report.calculation_inputs[0].period_semantics is InputPeriodSemantics.FLOW


def test_one_time_add_back_derived_is_flow_with_bound_period() -> None:
    rows = (_row("TXN-OT", row=0, amount="-1.00", description="Advisory retainer"),)
    links = (_link("TXN-OT", "P4", 0),)
    facts = (
        _fact(
            fact_id="ot-x",
            scenario_id="P4",
            kind=FactKind.ONE_TIME_ADD_BACK,
            payload=OneTimeAddBackPayload(
                label="Flood remediation",
                amount=MoneyAmount(value=Decimal("481247.63"), currency="USD"),
                counterparty="No Match Counterparty LLP",
                period_start=date(2025, 1, 1),
                period_end=date(2025, 12, 31),
            ),
        ),
    )
    defs = (
        _definition(
            "P4",
            MetricCategory.ONE_TIME_ADD_BACKS,
            definition_id="P4-ot",
        ).model_copy(
            update={
                "period": PeriodDefinition(
                    period_kind=PeriodKind.CLOSED_INTERVAL,
                    start_date=date(2025, 1, 1),
                    end_date=date(2025, 12, 31),
                )
            }
        ),
    )
    report = _run(rows, links, defs, facts)
    derived = [
        i for i in report.calculation_inputs if i.category is MetricCategory.ONE_TIME_ADD_BACKS
    ]
    assert derived
    assert all(i.period_semantics is InputPeriodSemantics.FLOW for i in derived)
    assert all(i.period_start == date(2025, 1, 1) for i in derived)
    assert all(i.period_end == date(2025, 12, 31) for i in derived)


def test_severance_is_as_of_with_source_date() -> None:
    rows = (_row("TXN-SV", row=0, amount="-10.00", description="Advisory retainer"),)
    links = (_link("TXN-SV", "P8", 0),)
    facts = (
        _fact(
            fact_id="sev-1",
            scenario_id="P8",
            kind=FactKind.OFF_LEDGER_AMOUNT,
            payload=OffLedgerAmountPayload(
                label="severance_liability",
                amount=MoneyAmount(value=Decimal("918447.52"), currency="USD"),
                as_of_date=date(2025, 12, 31),
            ),
        ),
    )
    defs = (
        _definition(
            "P8",
            MetricCategory.SEVERANCE_LIABILITY,
            definition_id="P8-sev",
        ).model_copy(
            update={
                "period": PeriodDefinition(
                    period_kind=PeriodKind.MIXED,
                    start_date=date(2025, 1, 1),
                    end_date=date(2025, 12, 31),
                    flow_start_date=date(2025, 1, 1),
                    flow_end_date=date(2025, 12, 31),
                    as_of_date=date(2025, 12, 31),
                )
            }
        ),
    )
    report = _run(rows, links, defs, facts)
    sev = next(
        i for i in report.calculation_inputs if i.category is MetricCategory.SEVERANCE_LIABILITY
    )
    assert sev.period_semantics is InputPeriodSemantics.AS_OF
    assert sev.as_of_date == date(2025, 12, 31)


def test_severance_binds_as_of_from_covenant_period_when_fact_omits_date() -> None:
    rows = (_row("TXN-SV2", row=0, amount="-10.00", description="Advisory retainer"),)
    links = (_link("TXN-SV2", "P8", 0),)
    facts = (
        _fact(
            fact_id="sev-2",
            scenario_id="P8",
            kind=FactKind.OFF_LEDGER_AMOUNT,
            payload=OffLedgerAmountPayload(
                label="severance_liability",
                amount=MoneyAmount(value=Decimal("918447.52"), currency="USD"),
                as_of_date=None,
            ),
        ),
    )
    defs = (
        _definition(
            "P8",
            MetricCategory.SEVERANCE_LIABILITY,
            definition_id="P8-sev2",
        ).model_copy(
            update={
                "period": PeriodDefinition(
                    period_kind=PeriodKind.MIXED,
                    as_of_date=date(2025, 12, 31),
                    flow_start_date=date(2025, 1, 1),
                    flow_end_date=date(2025, 12, 31),
                )
            }
        ),
    )
    report = _run(rows, links, defs, facts)
    sev = next(
        i for i in report.calculation_inputs if i.category is MetricCategory.SEVERANCE_LIABILITY
    )
    assert sev.period_semantics is InputPeriodSemantics.AS_OF
    assert sev.as_of_date == date(2025, 12, 31)
