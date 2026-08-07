"""Stage 5E.2 closure regressions (rejected reclass, ownership, eligibility, DeepSeek)."""

# ruff: noqa: RUF001

from __future__ import annotations

import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from halyk_agent.domain.authority.models import AuthorityDomain
from halyk_agent.domain.fact_extraction.engine import run_fact_extraction
from halyk_agent.domain.fact_extraction.extractors import extract_candidates
from halyk_agent.domain.fact_extraction.models import (
    FactKind,
    OwnershipPayload,
    ReclassificationDisposition,
    RelatedPartyThresholdPayload,
    RequirementTerminalState,
    TransactionPeriodPayload,
    TransactionReclassificationPayload,
)
from halyk_agent.domain.fact_extraction.requirements import derive_fact_requirements
from halyk_agent.domain.fact_extraction.windows import select_windows
from halyk_agent.domain.models_gateway.cache import cache_key
from halyk_agent.domain.models_gateway.gateway import LlmGatewayConfig, StructuredModelGateway
from halyk_agent.domain.models_gateway.providers.deepseek import DeepSeekStructuredProvider
from halyk_agent.domain.models_gateway.providers.mock import MockStructuredProvider
from halyk_agent.domain.models_gateway.types import (
    ExtractionState,
    StructuredExtractionRequest,
    StructuredExtractionResult,
)
from tests.authority.helpers import make_document
from tests.facts.helpers import make_decision, make_definition, make_requirement, reclass_modifier


def test_rejected_reclassification_ru_not_confirmed_none() -> None:
    text = (
        "(7.2) Операция TXN-X-0012, первоначально учтённая как Операционные расходы "
        "($118,447.52), рассматривалась на предмет возможной переклассификации как "
        "Страховые премии; по итогам разъяснений руководства первоначальная классификация "
        "(Операционные расходы) сохраняется, и корректировка для целей ковенантов "
        "не производилась. Основание: рассмотрено и отклонено."
    )
    doc = make_document(raw_text=text)
    report = run_fact_extraction(
        definitions=(make_definition(modifiers=(reclass_modifier(),)),),
        decisions=(
            make_decision(
                domain=AuthorityDomain.FINANCIAL_ADJUSTMENTS,
                winning=(doc.document_id,),
            ),
        ),
        documents=(doc,),
    )
    rejected = [
        f
        for f in report.accepted_facts
        if f.fact_kind is FactKind.TRANSACTION_RECLASSIFICATION
        and isinstance(f.payload, TransactionReclassificationPayload)
        and f.payload.disposition is ReclassificationDisposition.REJECTED
    ]
    assert rejected
    payload = rejected[0].payload
    assert isinstance(payload, TransactionReclassificationPayload)
    assert payload.transaction_id == "TXN-X-0012"
    assert payload.amount is not None
    assert payload.amount.value == Decimal("118447.52")
    assert payload.from_category and "Операционн" in payload.from_category
    assert payload.to_category and "Страхов" in payload.to_category
    result = next(
        r
        for r in report.requirement_results
        if r.fact_kind is FactKind.TRANSACTION_RECLASSIFICATION
    )
    assert result.terminal_state is RequirementTerminalState.RESOLVED
    assert result.terminal_state is not RequirementTerminalState.CONFIRMED_NONE


def test_rejected_reclassification_en_fixture() -> None:
    text = (
        "Transaction TXN-X-0021 ($1,204,663.28) was reviewed at lender request and "
        "considered for reclassification to Insurance Premiums from Operating Expenses; "
        "original classification remains and adjustment was not made / considered and rejected."
    )
    doc = make_document(raw_text=text)
    cands = extract_candidates(
        make_requirement(
            FactKind.TRANSACTION_RECLASSIFICATION,
            "reclass",
            domain=AuthorityDomain.FINANCIAL_ADJUSTMENTS,
        ),
        doc,
    )
    assert cands
    payload = cands[0].payload
    assert isinstance(payload, TransactionReclassificationPayload)
    assert payload.disposition is ReclassificationDisposition.REJECTED
    assert payload.transaction_id == "TXN-X-0021"


def test_generic_no_reclass_stays_confirmed_none() -> None:
    doc = make_document(raw_text="Переклассификаций за ковенантный период не требовалось.")
    report = run_fact_extraction(
        definitions=(make_definition(modifiers=(reclass_modifier(),)),),
        decisions=(
            make_decision(
                domain=AuthorityDomain.FINANCIAL_ADJUSTMENTS,
                winning=(doc.document_id,),
            ),
        ),
        documents=(doc,),
    )
    result = next(
        r
        for r in report.requirement_results
        if r.fact_kind is FactKind.TRANSACTION_RECLASSIFICATION
    )
    assert result.terminal_state is RequirementTerminalState.CONFIRMED_NONE
    assert not any(
        isinstance(f.payload, TransactionReclassificationPayload)
        and f.payload.disposition is ReclassificationDisposition.REJECTED
        for f in report.accepted_facts
    )


def test_multi_fact_reclass_accepted_and_rejected() -> None:
    text = (
        "(7.1) Сумма в размере $142,118.64, выплаченная контрагенту Tengiz Risk Engineering "
        "Bureau, первоначально учтённая как Операционные расходы, переклассифицирована для "
        "целей соблюдения ковенантов как Страховые премии.\n"
        "(7.2) Операция TXN-X-0012, первоначально учтённая как Операционные расходы "
        "($118,447.52), рассматривалась на предмет возможной переклассификации как "
        "Страховые премии; первоначальная классификация (Операционные расходы) сохраняется, "
        "и корректировка для целей ковенантов не производилась.\n"
        "(8.1) Операция TXN-X-0021 ($1,204,663.28, Acme LLP) была запрошена кредитором и "
        "проверена; корректировка для целей ковенантов не требуется, и её первоначальная "
        "классификация сохраняется."
    )
    doc = make_document(raw_text=text)
    report = run_fact_extraction(
        definitions=(make_definition(modifiers=(reclass_modifier(),)),),
        decisions=(
            make_decision(
                domain=AuthorityDomain.FINANCIAL_ADJUSTMENTS,
                winning=(doc.document_id,),
            ),
        ),
        documents=(doc,),
    )
    reclass = [
        f.payload
        for f in report.accepted_facts
        if isinstance(f.payload, TransactionReclassificationPayload)
    ]
    assert any(p.disposition is ReclassificationDisposition.ACCEPTED for p in reclass)
    rejected = [p for p in reclass if p.disposition is ReclassificationDisposition.REJECTED]
    assert len(rejected) >= 2
    result = next(
        r
        for r in report.requirement_results
        if r.fact_kind is FactKind.TRANSACTION_RECLASSIFICATION
    )
    assert result.terminal_state is RequirementTerminalState.RESOLVED
    assert len(result.accepted_fact_ids) >= 3


@pytest.mark.parametrize(
    ("row", "expected_name", "pct"),
    [
        ('"Saryarka Capital Partners" LLP 42.3%', "Saryarka Capital Partners LLP", "42.3"),
        ("«Turan Capital» LLP 28.8%", "Turan Capital LLP", "28.8"),
        ('"Example Holdings" JSC 31.2%', "Example Holdings JSC", "31.2"),
        ("Example Advisory Bureau LLP 23.4%", "Example Advisory Bureau LLP", "23.4"),
    ],
)
def test_ownership_quoted_names(row: str, expected_name: str, pct: str) -> None:
    doc = make_document(raw_text="Бенефициарное владение и контроль\n" + row + "\n")
    own = extract_candidates(
        make_requirement(
            FactKind.OWNERSHIP, "владе", "%", domain=AuthorityDomain.KYC_RELATIONSHIPS
        ),
        doc,
    )
    assert any(
        isinstance(c.payload, OwnershipPayload)
        and c.payload.entity_name == expected_name
        and c.payload.ownership_percent == Decimal(pct)
        for c in own
    )


@pytest.mark.parametrize("row", ["LLP 42.3%", '"LLP" 42.3%'])
def test_ownership_legal_form_only_negative(row: str) -> None:
    doc = make_document(raw_text="Бенефициарное владение\n" + row + "\n")
    own = extract_candidates(
        make_requirement(
            FactKind.OWNERSHIP, "владе", "%", domain=AuthorityDomain.KYC_RELATIONSHIPS
        ),
        doc,
    )
    names = [c.payload.entity_name for c in own if isinstance(c.payload, OwnershipPayload)]
    assert names == []


def _multi_page_document(*page_texts: str, artifact: str = "p2art") -> Any:
    from halyk_agent.domain.parsing import (
        BlockKind,
        CanonicalBlock,
        CanonicalDocument,
        CanonicalPage,
        ParserIdentity,
        ParserKind,
        ParseStatus,
        block_identity,
        compute_metrics,
        document_identity,
    )

    digest = "c" * 64
    doc_id = document_identity(artifact, digest)
    pages: list[CanonicalPage] = []
    for idx, raw_text in enumerate(page_texts, start=1):
        block = CanonicalBlock(
            id=block_identity(doc_id, idx, 0, BlockKind.PAGE_TEXT, raw_text, None),
            page_number=idx,
            ordinal=0,
            kind=BlockKind.PAGE_TEXT,
            raw_text=raw_text,
            normalized_text=raw_text,
            char_start=0,
            char_end=len(raw_text),
            source_parser=ParserKind.PYPDF,
            metadata={},
        )
        pages.append(
            CanonicalPage(
                page_number=idx,
                raw_text=raw_text,
                normalized_text=raw_text,
                blocks=[block],
            )
        )
    return CanonicalDocument(
        id=doc_id,
        artifact_id=artifact,
        document_id=doc_id,
        document_version_id="v1",
        source_file="p2.pdf",
        source_sha256=digest,
        parser=ParserIdentity(
            kind=ParserKind.PYPDF,
            package_name="pypdf",
            package_version="1",
            configuration_hash="c",
        ),
        status=ParseStatus.SUCCESS,
        pages=pages,
        metrics=compute_metrics(pages),
    )


def test_p2_shaped_ownership_table_deterministic() -> None:
    # Mojibake header + clean ASCII ownership/threshold rows (public P2 shape).
    header = "Р‘РµРЅРµС„РёС†РёР°СЂРЅРѕРµ РІР»Р°РґРµРЅРёРµ Рё РєРѕРЅС‚СЂРѕР»СЊ"
    threshold = (
        "РћСЂРіР°РЅРёР·Р°С†РёРё, РІ РєРѕС‚РѕСЂС‹С… Р“СЂСѓРїРїР° РІР»Р°РґРµРµС‚ 25.0% Рё Р±РѕР»РµРµ "
        "РіРѕР»РѕСЃСѓСЋС‰РёС… РїСЂР°РІ, РїСЂРёР·РЅР°СЋС‚СЃСЏ "
        "СЃРІСЏР·Р°РЅРЅС‹РјРё СЃС‚РѕСЂРѕРЅР°РјРё РґР»СЏ С†РµР»РµР№ Р”РѕРіРѕРІРѕСЂР°."
    )
    page2 = (
        f"{header}\n"
        "Almaty Chill Logistics LLP 8.6%\n"
        "Tien Shan Advisory Bureau 23.4%\n"
        "Zhetysu Capital Partners LLP 31.2%\n"
        f"{threshold}\n"
    )
    page3 = "Идентификация клиента. Структура владения пересматривается ежегодно.\n"
    page4 = "risk review структура владения без таблицы\n"
    doc = _multi_page_document("cover", page2, page3, page4)
    own = extract_candidates(
        make_requirement(
            FactKind.OWNERSHIP, "владе", "%", domain=AuthorityDomain.KYC_RELATIONSHIPS
        ),
        doc,
    )
    names = {c.payload.entity_name for c in own if isinstance(c.payload, OwnershipPayload)}
    assert "Almaty Chill Logistics LLP" in names
    assert "Tien Shan Advisory Bureau" in names
    assert "Zhetysu Capital Partners LLP" in names
    thr = extract_candidates(
        make_requirement(
            FactKind.RELATED_PARTY_THRESHOLD,
            "связанн",
            domain=AuthorityDomain.KYC_RELATIONSHIPS,
        ),
        doc,
    )
    assert thr
    assert isinstance(thr[0].payload, RelatedPartyThresholdPayload)
    assert thr[0].payload.threshold_percent == Decimal("25.0")
    assert thr[0].page_number == 2


def test_fx_policy_only_not_requirement() -> None:
    policy = (
        "Примечание 5 — Пересчёт операций в иностранной валюте\n"
        "Операции, выраженные в валютах, отличных от функциональной валюты, отражаются по "
        "обменным курсам, действующим на даты их совершения. Монетарные активы "
        "пересчитываются по курсу на отчётную дату."
    )
    doc = make_document(raw_text=policy)
    definitions = (make_definition(modifiers=(reclass_modifier(),)),)
    decisions = (
        make_decision(
            domain=AuthorityDomain.FINANCIAL_ADJUSTMENTS,
            winning=(doc.document_id,),
        ),
    )
    reqs = derive_fact_requirements(definitions, decisions, (doc,))
    assert not any(r.fact_kind is FactKind.FX_RATE for r in reqs)
    report = run_fact_extraction(definitions=definitions, decisions=decisions, documents=(doc,))
    assert not any(
        r.fact_kind is FactKind.FX_RATE and r.terminal_state is RequirementTerminalState.NEEDS_MODEL
        for r in report.requirement_results
    )


def test_subsidiary_weak_group_cue_absent_not_needs_model() -> None:
    from halyk_agent.domain.covenants.ast import MetricCategory, TransactionSelector

    text = (
        "Бенефициарное владение\n"
        "Ниже приведены прямые и косвенные доли участия Группы в организациях.\n"
        "Ertis Capital LLP 31.4%\n"
    )
    doc = make_document(raw_text=text)
    definitions = (
        make_definition(
            selectors=(
                TransactionSelector(
                    category=MetricCategory.CAPITAL_ASSET_TRANSFERS_TO_UNRESTRICTED_SUBS,
                ),
            )
        ),
    )
    decisions = (
        make_decision(
            domain=AuthorityDomain.KYC_RELATIONSHIPS,
            winning=(doc.document_id,),
        ),
    )
    report = run_fact_extraction(definitions=definitions, decisions=decisions, documents=(doc,))
    sub = next(r for r in report.requirement_results if r.fact_kind is FactKind.SUBSIDIARY_STATUS)
    assert sub.terminal_state is RequirementTerminalState.ABSENT_FROM_SOURCE
    assert sub.terminal_state is not RequirementTerminalState.NEEDS_MODEL


def test_window_must_contain_answer_for_needs_model() -> None:
    from halyk_agent.domain.fact_extraction.models import DerivationKind, FactRequirement

    req = FactRequirement(
        requirement_id="req-own",
        scenario_id="S1",
        fact_kind=FactKind.OWNERSHIP,
        derivation_kind=DerivationKind.SEMANTIC_REQUIRED,
        trigger_rule="test",
        allowed_authority_domains=(AuthorityDomain.KYC_RELATIONSHIPS,),
        reason_code="TEST",
        lexical_cues=("владе", "ownership", "%"),
        strong_lexical_cues=("владе", "ownership"),
    )
    doc = _multi_page_document(
        "Бенефициарное владение\nAcme Capital LLP 12.5%\n",
        "Общие сведения о структуре владения без таблицы долей.",
        artifact="winart",
    )
    window = select_windows(req, doc)
    assert window is not None
    assert any("12.5%" in f.text for f in window.fragments)
    assert all(f.page_number == 1 for f in window.fragments) or any(
        "12.5%" in f.text for f in window.fragments
    )


def test_period_evidence_covers_service_dates() -> None:
    from datetime import date

    text = (
        "Операция TXN-B4-0001 относится к услугам, оказанным в период с 2026-01-15 по 2026-03-20."
    )
    doc = make_document(raw_text=text)
    cands = extract_candidates(make_requirement(FactKind.TRANSACTION_PERIOD, "TXN-"), doc)
    assert cands
    payload = cands[0].payload
    assert isinstance(payload, TransactionPeriodPayload)
    assert payload.service_start == date(2026, 1, 15)
    assert payload.service_end == date(2026, 3, 20)
    assert "2026-01-15" in cands[0].quote
    assert "2026-03-20" in cands[0].quote


def _request(req_id: str = "req-1", window: str = "wh1") -> StructuredExtractionRequest:
    return StructuredExtractionRequest(
        requirement_id=req_id,
        scenario_id="S1",
        fact_kind="OWNERSHIP",
        authority_domain="KYC_RELATIONSHIPS",
        source_document_id="doc-1",
        source_sha256="b" * 64,
        window_hash=window,
        fragments=[{"fragment_id": "F001", "text": "Ertis Capital, LLP 31.4%"}],
    )


def test_deepseek_json_example_and_max_tokens(monkeypatch: Any) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    provider = DeepSeekStructuredProvider(thinking_enabled=False, max_tokens=2048)
    body = provider.build_request_body(_request())
    assert body["model"] == "deepseek-v4-flash"
    assert body["response_format"] == {"type": "json_object"}
    assert body["thinking"] == {"type": "disabled"}
    assert body["max_tokens"] == 2048
    user = body["messages"][1]["content"]
    assert "JSON" in body["messages"][0]["content"] or "JSON" in user
    assert "json_example" in user
    assert "OWNERSHIP" in user
    esc = DeepSeekStructuredProvider(thinking_enabled=True, reasoning_effort="high")
    esc_body = esc.build_request_body(_request())
    assert esc_body["thinking"] == {"type": "enabled"}
    assert esc_body["reasoning_effort"] == "high"
    assert esc_body["max_tokens"] == 2048


def test_usage_capture_and_missing_usage_ok(monkeypatch: Any) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    provider = DeepSeekStructuredProvider()
    good = MagicMock()
    good.raise_for_status = MagicMock()
    good.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "state": "UNRESOLVED",
                            "payload": None,
                            "evidence_fragment_ids": [],
                            "reason_code": "OK",
                        }
                    )
                }
            }
        ],
        "usage": {
            "prompt_tokens": 11,
            "completion_tokens": 7,
            "total_tokens": 18,
            "prompt_cache_hit_tokens": 2,
            "completion_tokens_details": {"reasoning_tokens": 3},
        },
    }
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.post.return_value = good
    with patch("httpx.Client", return_value=client):
        result = provider.extract(_request())
    assert result.usage is not None
    assert result.usage.prompt_tokens == 11
    assert result.usage.completion_tokens == 7
    assert result.usage.total_tokens == 18
    assert result.usage.prompt_cache_hit_tokens == 2
    assert result.usage.reasoning_tokens == 3
    assert result.latency_ms is not None and result.latency_ms >= 0

    good.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "state": "UNRESOLVED",
                            "payload": None,
                            "evidence_fragment_ids": [],
                            "reason_code": "OK",
                        }
                    )
                }
            }
        ]
    }
    with patch("httpx.Client", return_value=client):
        result2 = provider.extract(_request("req-2", "w2"))
    assert result2.state is ExtractionState.UNRESOLVED
    assert result2.usage is None


def test_latency_ms_populated_on_gateway_record(tmp_path: Path) -> None:
    mock = MockStructuredProvider(
        default_factory=lambda _r: StructuredExtractionResult(
            state=ExtractionState.UNRESOLVED,
            reason_code="NONE",
            latency_ms=12,
        )
    )
    gw = StructuredModelGateway(
        config=LlmGatewayConfig(
            allow_network=False,
            primary_provider="mock",
            cache_dir=tmp_path / "lat",
        ),
        mock=mock,
    )
    _, record = gw.extract(_request())
    assert record.latency_ms is not None
    assert record.latency_ms >= 0


def test_cache_revision_miss(tmp_path: Path) -> None:
    key_a = cache_key(
        provider="deepseek",
        model="deepseek-v4-flash",
        prompt_version="p1",
        schema_version="s1",
        requirement_id="r1",
        source_sha="a" * 64,
        window_hash="w1",
        gen_config={"provider_revision": "deepseek-v4-flash-2026-07-31", "max_tokens": 2048},
    )
    key_b = cache_key(
        provider="deepseek",
        model="deepseek-v4-flash",
        prompt_version="p1",
        schema_version="s1",
        requirement_id="r1",
        source_sha="a" * 64,
        window_hash="w1",
        gen_config={"provider_revision": "deepseek-v4-flash-2026-08-01", "max_tokens": 2048},
    )
    assert key_a != key_b
    provider = DeepSeekStructuredProvider()
    body = provider.build_request_body(_request())
    assert body["model"] == "deepseek-v4-flash"


def test_budget_claim_before_http_max_three(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    empty = MagicMock()
    empty.raise_for_status = MagicMock()
    empty.json.return_value = {"choices": [{"message": {"content": ""}}]}
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.post.return_value = empty

    primary = DeepSeekStructuredProvider(thinking_enabled=False)
    escalation = DeepSeekStructuredProvider(thinking_enabled=True, reasoning_effort="high")
    gw = StructuredModelGateway(
        config=LlmGatewayConfig(
            allow_network=True,
            primary_provider="deepseek",
            escalation_provider="deepseek",
            max_external_attempts=3,
            max_retries=1,
            max_thinking_escalations=2,
            cache_dir=tmp_path / "bud3",
        ),
        primary=primary,
        escalation=escalation,
    )
    with patch("httpx.Client", return_value=client):
        _r1, _ = gw.extract(_request("r1", "w1"))
        # primary empty + retry => 2 HTTP; may BUDGET or PROVIDER_ERROR
        assert primary.http_calls <= 3
        _r2, _ = gw.extract(_request("r2", "w2"))
        _r3, _ = gw.extract(_request("r3", "w3"))
        r4, _ = gw.extract(_request("r4", "w4"))
    assert client.post.call_count == 3
    assert gw.external_attempt_count == 3
    assert r4.state is ExtractionState.BUDGET_EXCEEDED


def test_budget_max_zero_no_http(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    primary = DeepSeekStructuredProvider()
    gw = StructuredModelGateway(
        config=LlmGatewayConfig(
            allow_network=True,
            primary_provider="deepseek",
            max_external_attempts=0,
            cache_dir=tmp_path / "bud0",
        ),
        primary=primary,
    )
    with patch("httpx.Client") as client_cls:
        result, _ = gw.extract(_request())
    assert result.state is ExtractionState.BUDGET_EXCEEDED
    assert primary.http_calls == 0
    assert client_cls.call_count == 0
    assert gw.external_attempt_count == 0


def test_import_order_subprocess() -> None:
    script = (
        "import halyk_agent.domain.models_gateway as m; "
        "import halyk_agent.domain.fact_extraction as f; "
        "assert m.StructuredModelGateway and f.run_fact_extraction; print('ok')"
    )
    script2 = (
        "import halyk_agent.domain.fact_extraction as f; "
        "import halyk_agent.domain.models_gateway as m; "
        "assert f.run_fact_extraction and m.StructuredModelGateway; print('ok')"
    )
    for code in (script, script2):
        proc = subprocess.run(
            [sys.executable, "-c", code],
            check=False,
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parents[2]),
            env={
                **dict(**{k: v for k, v in __import__("os").environ.items()}),
                "PYTHONPATH": "src",
            },
        )
        assert proc.returncode == 0, proc.stderr
        assert "ok" in proc.stdout
