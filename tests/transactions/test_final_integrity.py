"""Stage 5F.3 final input integrity & add-back semantics regressions."""

from __future__ import annotations

import json
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from halyk_agent.domain.authority.models import AuthorityDomain
from halyk_agent.domain.covenants.ast import MetricCategory, TransactionSelector
from halyk_agent.domain.fact_extraction.extractors import (
    extract_group_capex,
    ppe_roll_forward_is_closed,
)
from halyk_agent.domain.fact_extraction.models import (
    DerivationKind,
    FactKind,
    FactRequirementResult,
    GroupCapexDerivationType,
    MoneyAmount,
    OneTimeAddBackPayload,
    RequirementTerminalState,
)
from halyk_agent.domain.transaction_taxonomy.membership import (
    MEMBERSHIP_REASON_INCOME_TAX_EXCLUDED,
    MEMBERSHIP_REASON_ONE_TIME_ADD_BACK,
    MEMBERSHIP_REASON_TAX_SUBTYPE_UNPROVEN,
    membership_reasons,
)
from halyk_agent.domain.transaction_taxonomy.models import (
    SelectorReadinessStatus,
)
from halyk_agent.domain.transaction_taxonomy.selectors import input_matches_selector
from tests.authority.helpers import make_document
from tests.facts.helpers import make_requirement
from tests.transactions.test_semantic_correctness import (
    _definition,
    _fact,
    _link,
    _row,
    _run,
)


def test_attached_one_time_add_back_preserves_original_expense_membership() -> None:
    rows = (
        _row(
            "TXN-OT-1",
            row=0,
            amount="-100.00",
            description="Harbor dredging cleaning and maintenance",
            counterparty="Alpha Dredging LLP",
        ),
    )
    links = (_link("TXN-OT-1", "P4", 0),)
    facts = (
        _fact(
            fact_id="ot-1",
            scenario_id="P4",
            kind=FactKind.ONE_TIME_ADD_BACK,
            payload=OneTimeAddBackPayload(
                label="Dredging cleanup",
                amount=MoneyAmount(value=Decimal("100.00"), currency="USD"),
                counterparty="Alpha Dredging LLP",
            ),
        ),
    )
    defs = (
        _definition(
            "P4",
            MetricCategory.OPEX,
            MetricCategory.ONE_TIME_ADD_BACKS,
            definition_id="P4-ebitda",
        ),
    )
    report = _run(rows, links, defs, facts)
    assert len(report.calculation_inputs) == 1
    inp = report.calculation_inputs[0]
    assert inp.category is MetricCategory.OPEX
    assert MetricCategory.OPEX in inp.selector_categories
    assert MetricCategory.ONE_TIME_ADD_BACKS in inp.selector_categories
    assert MEMBERSHIP_REASON_ONE_TIME_ADD_BACK in inp.membership_reasons
    assert inp.metric_amount == Decimal("100.00")
    assert input_matches_selector(inp, TransactionSelector(category=MetricCategory.OPEX))
    assert input_matches_selector(
        inp, TransactionSelector(category=MetricCategory.ONE_TIME_ADD_BACKS)
    )


def test_public_shaped_p4_add_backs_share_semantics() -> None:
    rows = (
        _row(
            "TXN-P4-A",
            row=0,
            amount="-251338.94",
            description="Harbor dredging cleaning and maintenance",
            counterparty="Zhaiyk Dredging LLP",
        ),
        _row(
            "TXN-P4-B",
            row=1,
            amount="-342905.28",
            description="Freight arbitration consulting settlement",
            counterparty="Aga Freight Arbitration Bureau",
        ),
        _row(
            "TXN-P4-C",
            row=2,
            amount="-481247.63",
            description="Flood restoration repair works",
            counterparty="Pek Restoration Works LLP",
        ),
    )
    links = tuple(_link(r.txn_id, "P4", r.row_index) for r in rows)
    facts = (
        _fact(
            fact_id="ot-a",
            scenario_id="P4",
            kind=FactKind.ONE_TIME_ADD_BACK,
            payload=OneTimeAddBackPayload(
                label="Harbor dredging",
                amount=MoneyAmount(value=Decimal("251338.94"), currency="USD"),
                counterparty="Zhaiyk Dredging LLP",
            ),
        ),
        _fact(
            fact_id="ot-b",
            scenario_id="P4",
            kind=FactKind.ONE_TIME_ADD_BACK,
            payload=OneTimeAddBackPayload(
                label="Freight arbitration",
                amount=MoneyAmount(value=Decimal("342905.28"), currency="USD"),
                counterparty="Different Counterparty LLP",
            ),
        ),
        _fact(
            fact_id="ot-c",
            scenario_id="P4",
            kind=FactKind.ONE_TIME_ADD_BACK,
            payload=OneTimeAddBackPayload(
                label="Flood restoration",
                amount=MoneyAmount(value=Decimal("481247.63"), currency="USD"),
                counterparty="Another Counterparty LLP",
            ),
        ),
    )
    # Force unique attach only for A; B/C become fact-derived while ledger twins stay OPEX.
    report = _run(
        rows,
        links,
        (
            _definition(
                "P4",
                MetricCategory.OPEX,
                MetricCategory.ONE_TIME_ADD_BACKS,
                definition_id="P4-main",
            ),
        ),
        facts,
    )
    by_txn = {i.transaction_id: i for i in report.calculation_inputs if i.transaction_id}
    attached = by_txn["TXN-P4-A"]
    assert attached.category is MetricCategory.OPEX
    assert MetricCategory.ONE_TIME_ADD_BACKS in attached.selector_categories
    assert MetricCategory.OPEX in attached.selector_categories

    for tid in ("TXN-P4-B", "TXN-P4-C"):
        twin = by_txn[tid]
        assert twin.category is MetricCategory.OPEX
        assert MetricCategory.OPEX in twin.selector_categories
        assert MetricCategory.ONE_TIME_ADD_BACKS not in twin.selector_categories

    derived = [
        i
        for i in report.calculation_inputs
        if i.category is MetricCategory.ONE_TIME_ADD_BACKS and i.transaction_id is None
    ]
    assert len(derived) == 2
    ot_cov = next(
        c for c in report.selector_coverage if c.category is MetricCategory.ONE_TIME_ADD_BACKS
    )
    assert ot_cov.status is SelectorReadinessStatus.READY
    assert ot_cov.matching_input_count == 3


def test_ppe_only_no_disposals_is_incomplete() -> None:
    text = (
        "Note 7 — PPE\n"
        "Net book value at the beginning of the year $100.00\n"
        "Depreciation charge for the year $10.00\n"
        "Net book value at the end of the year $120.00\n"
        "There were no disposals.\n"
    )
    assert not ppe_roll_forward_is_closed(text)
    doc = make_document(raw_text=text)
    hits = extract_group_capex(
        make_requirement(FactKind.GROUP_CAPEX, "ppe", domain=AuthorityDomain.GROUP_STRUCTURE),
        doc,
        AuthorityDomain.GROUP_STRUCTURE,
    )
    assert hits == []


def test_ppe_fully_closed_bridge_derives_additions() -> None:
    text = (
        "Property, plant and equipment\n"
        "Net book value at the beginning of the year $100.00\n"
        "Depreciation charge for the year $10.00\n"
        "Net book value at the end of the year $120.00\n"
        "There were no disposals.\n"
        "There were no acquisitions.\n"
        "There were no transfers.\n"
        "There were no foreign exchange movements.\n"
        "There were no impairments.\n"
        "There were no revaluations.\n"
        "There were no other movements.\n"
    )
    assert ppe_roll_forward_is_closed(text)
    doc = make_document(raw_text=text)
    hits = extract_group_capex(
        make_requirement(FactKind.GROUP_CAPEX, "ppe", domain=AuthorityDomain.GROUP_STRUCTURE),
        doc,
        AuthorityDomain.GROUP_STRUCTURE,
    )
    assert len(hits) == 1
    payload = hits[0].payload
    assert payload.derivation_type is GroupCapexDerivationType.PPE_ROLL_FORWARD  # type: ignore[union-attr]
    assert payload.amount.value == Decimal("30.00")  # type: ignore[union-attr]


def test_mutated_accepted_facts_fail_hash_verification(tmp_path: Path) -> None:
    from halyk_agent.app.transactions import TransactionServiceError, transactions_from_paths

    smoke = Path("work/smoke5f2")
    if not (smoke / "facts" / "fact_extraction_manifest.json").exists():
        pytest.skip("smoke5f2 artifacts required")

    bundle = tmp_path / "bundle"
    for name in ("routing", "covenants", "facts"):
        shutil.copytree(smoke / name, bundle / name)

    accepted = bundle / "facts" / "accepted_facts.jsonl"
    lines = accepted.read_text(encoding="utf-8").splitlines()
    mutated: list[str] = []
    changed = False
    for line in lines:
        if not line.strip():
            continue
        obj = json.loads(line)
        payload = obj.get("payload") or {}
        amount = payload.get("amount")
        if (
            not changed
            and isinstance(amount, dict)
            and amount.get("value") is not None
            and obj.get("fact_kind") == "ONE_TIME_ADD_BACK"
        ):
            amount["value"] = "999999.99"
            payload["amount"] = amount
            obj["payload"] = payload
            changed = True
        mutated.append(json.dumps(obj, ensure_ascii=False, sort_keys=True))
    assert changed
    accepted.write_text("\n".join(mutated) + "\n", encoding="utf-8")

    out = tmp_path / "tx-out"
    out.mkdir()
    marker = out / "stage5f_manifest.json"
    marker.write_text('{"keep":true}\n', encoding="utf-8")

    with pytest.raises(TransactionServiceError) as exc:
        transactions_from_paths(
            routing_dir=bundle / "routing",
            covenants_dir=bundle / "covenants",
            facts_dir=bundle / "facts",
            ledger_path=Path("agentic-bank-public/master_ledger_2025.csv"),
            output_dir=out,
            overwrite=True,
        )
    assert exc.value.code == "FACT_ARTIFACT_HASH_MISMATCH"
    assert marker.exists()
    assert json.loads(marker.read_text(encoding="utf-8"))["keep"] is True


def test_incomplete_fact_only_severance_cannot_true_zero() -> None:
    rows = (_row("TXN-SV-1", row=0, amount="-10.00", description="Advisory retainer"),)
    links = (_link("TXN-SV-1", "P8", 0),)
    defs = (_definition("P8", MetricCategory.SEVERANCE_LIABILITY, definition_id="P8-sev"),)
    incomplete = (
        FactRequirementResult(
            requirement_id="req-sev",
            scenario_id="P8",
            fact_kind=FactKind.OFF_LEDGER_AMOUNT,
            derivation_kind=DerivationKind.SEMANTIC_REQUIRED,
            terminal_state=RequirementTerminalState.ABSENT_FROM_SOURCE,
            reason_code="UNRESOLVED_SOURCE_QUALITY",
        ),
    )
    report = _run(rows, links, defs, fact_requirement_results=incomplete)
    cov = next(c for c in report.selector_coverage if c.definition_id == "P8-sev")
    assert cov.status is SelectorReadinessStatus.UNRESOLVED
    assert cov.reason_code == "UNRESOLVED_SOURCE_QUALITY"


def test_confirmed_none_severance_may_true_zero() -> None:
    rows = (_row("TXN-SV-2", row=0, amount="-10.00", description="Advisory retainer"),)
    links = (_link("TXN-SV-2", "P8", 0),)
    defs = (_definition("P8", MetricCategory.SEVERANCE_LIABILITY, definition_id="P8-sev2"),)
    confirmed = (
        FactRequirementResult(
            requirement_id="req-sev2",
            scenario_id="P8",
            fact_kind=FactKind.OFF_LEDGER_AMOUNT,
            derivation_kind=DerivationKind.SEMANTIC_REQUIRED,
            terminal_state=RequirementTerminalState.CONFIRMED_NONE,
            reason_code="CONFIRMED_NONE",
        ),
    )
    report = _run(rows, links, defs, fact_requirement_results=confirmed)
    cov = next(c for c in report.selector_coverage if c.definition_id == "P8-sev2")
    assert cov.status is SelectorReadinessStatus.TRUE_ZERO


def test_tax_membership_reason_codes_split() -> None:
    income = membership_reasons(MetricCategory.TAXES, description="Corporate income tax instalment")
    assert MEMBERSHIP_REASON_INCOME_TAX_EXCLUDED in income
    unknown = membership_reasons(MetricCategory.TAXES, description="Miscellaneous fiscal charge")
    assert MEMBERSHIP_REASON_TAX_SUBTYPE_UNPROVEN in unknown
