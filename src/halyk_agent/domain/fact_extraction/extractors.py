"""Deterministic FactKind extractors (generic RU/EN patterns; no scenario hardcoding)."""

# ruff: noqa: RUF001

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date
from decimal import Decimal

from halyk_agent.domain.authority.models import AuthorityDomain
from halyk_agent.domain.fact_extraction.entity_quality import is_meaningful_entity_name
from halyk_agent.domain.fact_extraction.models import (
    AmountCorrectionPayload,
    ExtractionMethod,
    FactCandidate,
    FactKind,
    FactRequirement,
    FxRatePayload,
    MoneyAmount,
    OffLedgerAmountPayload,
    OneTimeAddBackPayload,
    OwnershipPayload,
    PeriodDisposition,
    RateSource,
    ReclassificationDisposition,
    RelatedPartyThresholdPayload,
    SubsidiaryKind,
    SubsidiaryStatusPayload,
    TransactionPeriodPayload,
    TransactionReclassificationPayload,
    TransactionTreatmentPayload,
    TreatmentDisposition,
)
from halyk_agent.domain.fact_extraction.text_locate import (
    TXN_ID_RE,
    find_txn_ids,
    page_text_slices,
    parse_money,
    parse_percentage,
)
from halyk_agent.domain.ids import deterministic_id
from halyk_agent.domain.parsing import CanonicalDocument

ExtractorFn = Callable[
    [FactRequirement, CanonicalDocument, AuthorityDomain],
    list[FactCandidate],
]


def _parse_iso_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        return None


def _candidate(
    *,
    requirement: FactRequirement,
    document: CanonicalDocument,
    authority_domain: AuthorityDomain,
    kind: FactKind,
    payload: object,
    quote: str,
    page_number: int,
    char_start: int,
    char_end: int,
    reason_code: str,
) -> FactCandidate:
    return FactCandidate(
        candidate_id=deterministic_id(
            "fact-candidate",
            requirement.requirement_id,
            document.document_id,
            kind.value,
            reason_code,
            quote,
            str(page_number),
            str(char_start),
            str(char_end),
        ),
        requirement_id=requirement.requirement_id,
        scenario_id=requirement.scenario_id,
        fact_kind=kind,
        payload=payload,  # type: ignore[arg-type]
        authority_domain=authority_domain,
        source_document_id=document.document_id,
        source_file=document.source_file,
        source_sha256=document.source_sha256,
        extraction_method=ExtractionMethod.DETERMINISTIC,
        reason_code=reason_code,
        quote=quote.strip(),
        page_number=page_number,
        char_start=char_start,
        char_end=char_end,
    )


_RECLASS_RU = re.compile(
    r"(?P<body>"
    r"Сумма(?:\s+в\s+размере)?\s+(?P<money>\$[\d,]+(?:\.\d{2})?|"
    r"€[\d,]+(?:\.\d{2})?|USD\s*[\d,]+(?:\.\d{2})?)"
    r".{0,200}?"
    r"(?:контрагент(?:у|ом)?|выплаченн\w*\s+контрагент\w*)\s+"
    r"(?P<cp>[A-Za-zА-Яа-яЁё0-9][^,\n]{1,80}?)"
    r"\s*,\s*.{0,100}?"
    r"(?:учт[её]нн\w*|отраж[её]нн\w*|classified|recorded)\s+как\s+"
    r"(?P<from>[^,\n.]{2,80})"
    r"\s*,\s*.{0,80}?"
    r"(?:перекласс\w*|переквалиф\w*|reclass\w*)"
    r".{0,120}?"
    r"как\s+(?P<to>[A-Za-zА-Яа-яЁё][^,\n.]{2,80})"
    r")",
    re.IGNORECASE | re.DOTALL,
)

_RECLASS_EN = re.compile(
    r"(?P<body>"
    r"(?:Amount|Sum)\s+(?:of\s+)?(?P<money>\$[\d,]+(?:\.\d{2})?)"
    r"[^.]{0,120}?"
    r"(?:counterparty|paid\s+to)\s+(?P<cp>[A-Za-z0-9][\w .,&'\-]{1,80}?)"
    r"[^.]{0,80}?"
    r"(?:recorded|classified)\s+as\s+(?P<from>[^,\n.]{2,80})"
    r"[^.]{0,60}?"
    r"reclass\w+"
    r"[^.]{0,80}?"
    r"as\s+(?P<to>[^,\n.]{2,80})"
    r")",
    re.IGNORECASE | re.DOTALL,
)

_REJECTED_MARKERS = ("отклон", "отверг", "rejected", "не принят", "непринят")


def _clean_category(raw: str) -> str:
    """Normalize captured category text (stop at clause/purpose connectors)."""
    text = raw.strip(" ,.;:\n\t")
    text = re.split(
        r"\s+(?:для\s+целей|был|была|были|was|were|for\s+covenant|Основание)\b",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    return text.strip(" ,.;:\n\t")


def extract_reclassification(
    requirement: FactRequirement,
    document: CanonicalDocument,
    authority_domain: AuthorityDomain,
) -> list[FactCandidate]:
    out: list[FactCandidate] = []
    for page_number, text in page_text_slices(document):
        for pattern in (_RECLASS_RU, _RECLASS_EN):
            for match in pattern.finditer(text):
                body = match.group("body").strip()
                money = parse_money(match.group("money"))
                if money is None:
                    continue
                amount = MoneyAmount(value=money[0], currency=money[1])
                cp = match.group("cp").strip(" ,.;")
                from_cat = _clean_category(match.group("from"))
                to_cat = _clean_category(match.group("to"))
                if not from_cat or not to_cat or from_cat.casefold() == to_cat.casefold():
                    continue
                context = text[match.start() : min(len(text), match.end() + 120)].casefold()
                disposition = (
                    ReclassificationDisposition.REJECTED
                    if any(m in context for m in _REJECTED_MARKERS)
                    else ReclassificationDisposition.ACCEPTED
                )
                txn_ids = find_txn_ids(body)
                txn_id = txn_ids[0] if txn_ids else None
                payload = TransactionReclassificationPayload(
                    transaction_id=txn_id,
                    counterparty=cp or None,
                    amount=amount,
                    from_category=from_cat,
                    to_category=to_cat,
                    disposition=disposition,
                )
                out.append(
                    _candidate(
                        requirement=requirement,
                        document=document,
                        authority_domain=authority_domain,
                        kind=FactKind.TRANSACTION_RECLASSIFICATION,
                        payload=payload,
                        quote=text[match.start("body") : match.end("body")],
                        page_number=page_number,
                        char_start=match.start("body"),
                        char_end=match.end("body"),
                        reason_code="DET_RECLASS",
                    )
                )
    return out


_PERIOD_EXCLUDE = re.compile(
    r"(?P<body>(?:Операция\s+)?(?P<txn>TXN-[A-Za-z0-9]+-\d+)"
    r"[^.\n]{0,160}?"
    r"(?:исключен\w*\s+из\s+(?:ковенантн\w*\s+)?период\w*"
    r"|excluded\s+from\s+(?:the\s+)?(?:covenant\s+)?period)"
    r"(?:\s+(?P<label>\d{4}\s*года|\d{4}|[^.\n]{2,40}))?)",
    re.IGNORECASE | re.DOTALL,
)
_PERIOD_ASSIGN = re.compile(
    r"(?P<body>(?:Операция\s+)?(?P<txn>TXN-[A-Za-z0-9]+-\d+)"
    r"[^.\n]{0,160}?"
    r"(?:относит\w*\s+к\s+(?:услуг\w*|ковенантн\w*\s+период\w*|период\w*)"
    r"|assign(?:ed)?\s+to\s+(?:the\s+)?(?:covenant\s+)?period"
    r"|оказанн\w*\s+в\s+период)"
    r"(?:\s+(?:с\s+)?(?P<label>[^.\n]{2,80}))?)",
    re.IGNORECASE | re.DOTALL,
)
_SERVICE_RANGE = re.compile(
    r"(?:с|from)\s+(?P<start>\d{4}-\d{2}-\d{2})\s+(?:по|to|until|-)\s+(?P<end>\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)


def _period_dates(text: str, match_start: int, match_end: int) -> tuple[date | None, date | None]:
    window = text[max(0, match_start - 80) : min(len(text), match_end + 120)]
    m = _SERVICE_RANGE.search(window)
    if m is None:
        return None, None
    return _parse_iso_date(m.group("start")), _parse_iso_date(m.group("end"))


def extract_period(
    requirement: FactRequirement,
    document: CanonicalDocument,
    authority_domain: AuthorityDomain,
) -> list[FactCandidate]:
    out: list[FactCandidate] = []
    for page_number, text in page_text_slices(document):
        for match in _PERIOD_EXCLUDE.finditer(text):
            body = match.group("body").strip()
            label = (match.group("label") or "").strip(" ,.;") or None
            start, end = _period_dates(text, match.start(), match.end())
            payload = TransactionPeriodPayload(
                transaction_id=match.group("txn"),
                disposition=PeriodDisposition.EXCLUDE_FROM_PERIOD,
                period_label=label,
                service_start=start,
                service_end=end,
            )
            out.append(
                _candidate(
                    requirement=requirement,
                    document=document,
                    authority_domain=authority_domain,
                    kind=FactKind.TRANSACTION_PERIOD,
                    payload=payload,
                    quote=body[:500],
                    page_number=page_number,
                    char_start=match.start(),
                    char_end=match.end(),
                    reason_code="DET_PERIOD_EXCLUDE",
                )
            )
        for match in _PERIOD_ASSIGN.finditer(text):
            body = match.group("body").strip()
            label = (match.group("label") or "").strip(" ,.;") or None
            start, end = _period_dates(text, match.start(), match.end())
            # Prefer explicit service range as label when present.
            if start and end and not label:
                label = f"{start.isoformat()}..{end.isoformat()}"
            payload = TransactionPeriodPayload(
                transaction_id=match.group("txn"),
                disposition=PeriodDisposition.ASSIGN_TO_PERIOD,
                period_label=label,
                service_start=start,
                service_end=end,
            )
            out.append(
                _candidate(
                    requirement=requirement,
                    document=document,
                    authority_domain=authority_domain,
                    kind=FactKind.TRANSACTION_PERIOD,
                    payload=payload,
                    quote=body[:500],
                    page_number=page_number,
                    char_start=match.start(),
                    char_end=match.end(),
                    reason_code="DET_PERIOD_ASSIGN",
                )
            )
    return out


_OWNERSHIP_ROW = re.compile(
    r"(?P<body>(?P<entity>[A-Za-zА-Яа-яЁё][\w .,&'\-]{1,80}?)\s+"
    r"(?P<pct>\d+(?:[.,]\d+)?)\s*%)",
    re.UNICODE,
)


def extract_ownership(
    requirement: FactRequirement,
    document: CanonicalDocument,
    authority_domain: AuthorityDomain,
) -> list[FactCandidate]:
    ownership_cues = ("владе", "ownership", "голосующ", "бенефициар", "доли участия")
    out: list[FactCandidate] = []
    for page_number, text in page_text_slices(document):
        lowered = text.casefold()
        if not any(cue in lowered for cue in ownership_cues):
            continue
        for match in _OWNERSHIP_ROW.finditer(text):
            entity = match.group("entity").strip(" :-\t")
            # Skip table headers / boilerplate.
            if entity.casefold() in {
                "организация",
                "entity",
                "организация доля голосующих прав",
            }:
                continue
            if "доля" in entity.casefold() and len(entity) < 40:
                continue
            if not is_meaningful_entity_name(entity):
                continue
            pct = parse_percentage(match.group(0))
            if pct is None:
                continue
            # Prefer corporate-looking names (LLP/JSC/Inc or Latin letters).
            if not re.search(r"[A-Za-z]|ООО|АО|ТОО|LLP|JSC|Inc", entity):
                continue
            payload = OwnershipPayload(
                entity_name=entity,
                ownership_percent=pct,
            )
            out.append(
                _candidate(
                    requirement=requirement,
                    document=document,
                    authority_domain=authority_domain,
                    kind=FactKind.OWNERSHIP,
                    payload=payload,
                    quote=match.group("body").strip(),
                    page_number=page_number,
                    char_start=match.start(),
                    char_end=match.end(),
                    reason_code="DET_OWNERSHIP",
                )
            )
    return out


_RP_THRESHOLD = re.compile(
    r"(?P<body>(?:владеет|owns)\s+(?P<pct>\d+(?:[.,]\d+)?)\s*%\s+"
    r"(?:и\s+более|or\s+more|или\s+более)[^.]{0,80}?"
    r"(?:связанн\w*\s+сторон\w*|related\s+part(?:y|ies)))",
    re.IGNORECASE | re.DOTALL,
)


def extract_related_party_threshold(
    requirement: FactRequirement,
    document: CanonicalDocument,
    authority_domain: AuthorityDomain,
) -> list[FactCandidate]:
    out: list[FactCandidate] = []
    for page_number, text in page_text_slices(document):
        for match in _RP_THRESHOLD.finditer(text):
            pct = parse_percentage(match.group("pct") + "%")
            if pct is None:
                continue
            body = match.group("body").strip()
            payload = RelatedPartyThresholdPayload(threshold_percent=pct)
            out.append(
                _candidate(
                    requirement=requirement,
                    document=document,
                    authority_domain=authority_domain,
                    kind=FactKind.RELATED_PARTY_THRESHOLD,
                    payload=payload,
                    quote=body[:400],
                    page_number=page_number,
                    char_start=match.start(),
                    char_end=match.end(),
                    reason_code="DET_RP_THRESHOLD",
                )
            )
    return out


_SEVERANCE = re.compile(
    r"(?P<body>(?:выходн\w*\s+пособи\w*|severance(?:\s+liabilit\w*)?)"
    r"[^.]{0,80}?(?:в\s+размере|of|amount(?:ing)?\s+to)?\s*"
    r"(?P<money>\$[\d,]+(?:\.\d{2})?|€[\d,]+(?:\.\d{2})?))",
    re.IGNORECASE | re.DOTALL,
)


def extract_off_ledger(
    requirement: FactRequirement,
    document: CanonicalDocument,
    authority_domain: AuthorityDomain,
) -> list[FactCandidate]:
    out: list[FactCandidate] = []
    for page_number, text in page_text_slices(document):
        for match in _SEVERANCE.finditer(text):
            money = parse_money(match.group("money"))
            if money is None:
                continue
            body = match.group("body").strip()
            payload = OffLedgerAmountPayload(
                label="severance_liability",
                amount=MoneyAmount(value=money[0], currency=money[1]),
            )
            out.append(
                _candidate(
                    requirement=requirement,
                    document=document,
                    authority_domain=authority_domain,
                    kind=FactKind.OFF_LEDGER_AMOUNT,
                    payload=payload,
                    quote=body[:400],
                    page_number=page_number,
                    char_start=match.start(),
                    char_end=match.end(),
                    reason_code="DET_OFF_LEDGER",
                )
            )
    return out


_SUBSIDIARY = re.compile(
    r"(?P<body>"
    r"(?:"
    r"(?P<entity>[A-Za-zА-Яа-яЁё][\w .,&'\-]{2,80}?)\s+"
    r"(?:является|is(?:\s+an?)?)\s+"
    r"(?P<status>"
    r"ограниченн\w*\s+дочерн\w*|неограниченн\w*\s+дочерн\w*"
    r"|restricted\s+subsidiar\w*|unrestricted\s+subsidiar\w*"
    r"|member\s+of\s+(?:the\s+)?(?:consolidated\s+)?group"
    r"|член\w*\s+групп\w*"
    r")"
    r")"
    r"|"
    r"(?:conducted|operated|reported)\s+through\s+"
    r"(?P<entity2>[A-Za-z][\w .,&'\-\n]{2,80}?(?:JSC|LLP|Inc|АО|ТОО))"
    r"|"
    r"(?:standalone\s+)?(?P<status2>subsidiar\w+|дочерн\w+\s+компани\w+)"
    r"[^.]{0,120}?(?P<entity3>[A-Za-z][\w .,&'\-\n]{2,80}?(?:JSC|LLP|Inc|АО|ТОО))"
    r")",
    re.IGNORECASE | re.DOTALL,
)


def extract_subsidiary_status(
    requirement: FactRequirement,
    document: CanonicalDocument,
    authority_domain: AuthorityDomain,
) -> list[FactCandidate]:
    out: list[FactCandidate] = []
    for page_number, text in page_text_slices(document):
        for match in _SUBSIDIARY.finditer(text):
            status_raw = (match.group("status") or match.group("status2") or "").casefold()
            if match.groupdict().get("entity2"):
                status = SubsidiaryKind.GROUP_MEMBER
            elif "неограничен" in status_raw or "unrestricted" in status_raw:
                status = SubsidiaryKind.UNRESTRICTED
            elif (
                "ограничен" in status_raw or "restricted" in status_raw
            ) and "unrestricted" not in status_raw:
                status = SubsidiaryKind.RESTRICTED
            else:
                status = SubsidiaryKind.GROUP_MEMBER
            entity = match.group("entity") or match.group("entity2") or match.group("entity3") or ""
            entity = re.sub(r"\s+", " ", entity).strip(" ,.;")
            if not entity:
                continue
            body = text[match.start() : match.end()]
            payload = SubsidiaryStatusPayload(entity_name=entity, status=status)
            out.append(
                _candidate(
                    requirement=requirement,
                    document=document,
                    authority_domain=authority_domain,
                    kind=FactKind.SUBSIDIARY_STATUS,
                    payload=payload,
                    quote=body,
                    page_number=page_number,
                    char_start=match.start(),
                    char_end=match.end(),
                    reason_code="DET_SUBSIDIARY",
                )
            )
    return out


_FX = re.compile(
    r"(?P<body>(?:обменн\w*\s+курс|exchange\s+rate|курс)\s+"
    r"(?:of\s+|равен\s+|составил\s+|is\s+)?"
    r"(?P<rate>\d+(?:[.,]\d+)?)"
    r"[^.\n]{0,40}?"
    r"(?:(?P<from>[A-Z]{3})\s*/\s*(?P<to>[A-Z]{3})"
    r"|(?P<from2>USD|EUR|GBP|KZT)\s*(?:к|to|/|->)\s*(?P<to2>USD|EUR|GBP|KZT)))",
    re.IGNORECASE,
)

# Invoice in foreign currency settled in another currency — preserve amounts; never invent rate.
_FX_SETTLEMENT = re.compile(
    r"(?P<body>(?:счёт|invoice|amount)\s+на\s+сумму\s+"
    r"(?P<foreign>[\d,]+(?:\.\d+)?)\s*(?P<from>EUR|GBP|KZT|USD)"
    r"[^.]{0,120}?"
    r"(?:урегулирован|settled|оплачен)\w*"
    r"[^.]{0,80}?"
    r"(?:размере|of|amount)\s*"
    r"(?P<money>\$[\d,]+(?:\.\d{2})?|(?:USD|EUR|GBP|KZT)\s*[\d,]+(?:\.\d{2})?))",
    re.IGNORECASE | re.DOTALL,
)


def extract_fx_rate(
    requirement: FactRequirement,
    document: CanonicalDocument,
    authority_domain: AuthorityDomain,
) -> list[FactCandidate]:
    out: list[FactCandidate] = []
    for page_number, text in page_text_slices(document):
        for match in _FX.finditer(text):
            rate_raw = match.group("rate").replace(",", ".")
            try:
                rate = Decimal(rate_raw)
            except Exception:
                continue
            if rate <= 0:
                continue
            from_c = (match.group("from") or match.group("from2") or "").upper()
            to_c = (match.group("to") or match.group("to2") or "").upper()
            if not from_c or not to_c:
                continue
            body = match.group("body").strip()
            txn = find_txn_ids(body)
            payload = FxRatePayload(
                from_currency=from_c,
                to_currency=to_c,
                explicit_rate=rate,
                rate_source=RateSource.EXPLICIT,
                transaction_id=txn[0] if txn else None,
            )
            out.append(
                _candidate(
                    requirement=requirement,
                    document=document,
                    authority_domain=authority_domain,
                    kind=FactKind.FX_RATE,
                    payload=payload,
                    quote=body[:400],
                    page_number=page_number,
                    char_start=match.start(),
                    char_end=match.end(),
                    reason_code="DET_FX",
                )
            )
        for match in _FX_SETTLEMENT.finditer(text):
            foreign_raw = match.group("foreign").replace(",", "")
            money = parse_money(match.group("money"))
            if money is None:
                continue
            try:
                foreign = Decimal(foreign_raw)
            except Exception:
                continue
            if foreign <= 0 or money[0] <= 0:
                continue
            from_c = match.group("from").upper()
            to_c = money[1]
            if from_c == to_c:
                continue
            body = match.group("body").strip()
            txn = find_txn_ids(text[max(0, match.start() - 80) : match.end() + 40])
            # Source-faithful: keep both amounts; do NOT calculate a rate.
            payload = FxRatePayload(
                from_currency=from_c,
                to_currency=to_c,
                source_amount=MoneyAmount(value=foreign, currency=from_c),
                settlement_amount=MoneyAmount(value=money[0], currency=to_c),
                explicit_rate=None,
                rate_source=RateSource.NOT_STATED,
                transaction_id=txn[0] if txn else None,
            )
            out.append(
                _candidate(
                    requirement=requirement,
                    document=document,
                    authority_domain=authority_domain,
                    kind=FactKind.FX_RATE,
                    payload=payload,
                    quote=body[:400],
                    page_number=page_number,
                    char_start=match.start(),
                    char_end=match.end(),
                    reason_code="DET_FX_SETTLEMENT",
                )
            )
    return out


_ONE_TIME = re.compile(
    r"(?P<body>(?:единовременн\w*|one[-\s]?time|add[-\s]?back)[^.]{0,100}?"
    r"(?P<money>\$[\d,]+(?:\.\d{2})?|€[\d,]+(?:\.\d{2})?))",
    re.IGNORECASE | re.DOTALL,
)


def extract_one_time_add_back(
    requirement: FactRequirement,
    document: CanonicalDocument,
    authority_domain: AuthorityDomain,
) -> list[FactCandidate]:
    out: list[FactCandidate] = []
    for page_number, text in page_text_slices(document):
        for match in _ONE_TIME.finditer(text):
            money = parse_money(match.group("money"))
            if money is None:
                continue
            body = match.group("body").strip()
            payload = OneTimeAddBackPayload(
                label="one_time_add_back",
                amount=MoneyAmount(value=money[0], currency=money[1]),
            )
            out.append(
                _candidate(
                    requirement=requirement,
                    document=document,
                    authority_domain=authority_domain,
                    kind=FactKind.ONE_TIME_ADD_BACK,
                    payload=payload,
                    quote=body[:400],
                    page_number=page_number,
                    char_start=match.start(),
                    char_end=match.end(),
                    reason_code="DET_ONE_TIME",
                )
            )
    return out


_AMOUNT_CORR = re.compile(
    r"(?P<body>(?:(?:уточн[её]нн\w*|исправленн\w*|correct(?:ed)?)\s+)?"
    r"(?:сумма|amount)\s+"
    r"(?:корректировк\w*|correction|adjusted\s+to|should\s+read|должна\s+составлять)?\s*"
    r"(?P<money>\$[\d,]+(?:\.\d{2})?)"
    r"(?:[^.]{0,60}?(?P<txn>TXN-[A-Za-z0-9]+-\d+))?)",
    re.IGNORECASE,
)

_AMOUNT_MISSING_LEDGER = re.compile(
    r"(?P<body>(?:Операция\s+)?(?P<txn>TXN-[A-Za-z0-9]+-\d+)"
    r"[^.]{0,200}?"
    r"(?:сумма\s+не\s+отражен\w*|amount\s+(?:is\s+)?(?:missing|not\s+reflected)|не\s+отражена\s+в\s+выгрузке)"
    r"[^.]{0,160}?"
    r"(?:фактическ\w*\s+сумм\w*|actual\s+amount|составляет|is)\s*"
    r"(?P<money>\$[\d,]+(?:\.\d{2})?))",
    re.IGNORECASE | re.DOTALL,
)


def extract_amount_correction(
    requirement: FactRequirement,
    document: CanonicalDocument,
    authority_domain: AuthorityDomain,
) -> list[FactCandidate]:
    out: list[FactCandidate] = []
    for page_number, text in page_text_slices(document):
        for pattern, reason in (
            (_AMOUNT_CORR, "DET_AMOUNT_CORR"),
            (_AMOUNT_MISSING_LEDGER, "DET_AMOUNT_MISSING_LEDGER"),
        ):
            for match in pattern.finditer(text):
                money = parse_money(match.group("money"))
                if money is None:
                    continue
                body = match.group("body").strip()
                txn = match.groupdict().get("txn")
                payload = AmountCorrectionPayload(
                    transaction_id=txn,
                    amount=MoneyAmount(value=money[0], currency=money[1]),
                    description=reason.lower(),
                )
                out.append(
                    _candidate(
                        requirement=requirement,
                        document=document,
                        authority_domain=authority_domain,
                        kind=FactKind.AMOUNT_CORRECTION,
                        payload=payload,
                        quote=body[:400],
                        page_number=page_number,
                        char_start=match.start(),
                        char_end=match.end(),
                        reason_code=reason,
                    )
                )
    return out


_TREATMENT = re.compile(
    r"(?P<body>(?P<txn>TXN-[A-Za-z0-9]+-\d+)\s+"
    r"(?P<disp>исключа\w*|включа\w*|exclud\w*|includ\w+)"
    r"[^.]{0,40}?(?:из\s+расчёта|в\s+расчёт|from\s+(?:the\s+)?covenant|in\s+(?:the\s+)?covenant)?)",
    re.IGNORECASE,
)


def extract_treatment(
    requirement: FactRequirement,
    document: CanonicalDocument,
    authority_domain: AuthorityDomain,
) -> list[FactCandidate]:
    out: list[FactCandidate] = []
    for page_number, text in page_text_slices(document):
        for match in _TREATMENT.finditer(text):
            disp_raw = match.group("disp").casefold()
            if disp_raw.startswith("исключ") or disp_raw.startswith("exclud"):
                disposition = TreatmentDisposition.EXCLUDE
            elif disp_raw.startswith("включа") or disp_raw.startswith("includ"):
                disposition = TreatmentDisposition.INCLUDE
            else:
                continue
            body = match.group("body").strip()
            payload = TransactionTreatmentPayload(
                transaction_id=match.group("txn"),
                disposition=disposition,
            )
            out.append(
                _candidate(
                    requirement=requirement,
                    document=document,
                    authority_domain=authority_domain,
                    kind=FactKind.TRANSACTION_TREATMENT,
                    payload=payload,
                    quote=body[:400],
                    page_number=page_number,
                    char_start=match.start(),
                    char_end=match.end(),
                    reason_code="DET_TREATMENT",
                )
            )
    return out


_EXTRACTORS: dict[FactKind, ExtractorFn] = {
    FactKind.TRANSACTION_RECLASSIFICATION: extract_reclassification,
    FactKind.TRANSACTION_PERIOD: extract_period,
    FactKind.OWNERSHIP: extract_ownership,
    FactKind.RELATED_PARTY_THRESHOLD: extract_related_party_threshold,
    FactKind.OFF_LEDGER_AMOUNT: extract_off_ledger,
    FactKind.SUBSIDIARY_STATUS: extract_subsidiary_status,
    FactKind.FX_RATE: extract_fx_rate,
    FactKind.ONE_TIME_ADD_BACK: extract_one_time_add_back,
    FactKind.AMOUNT_CORRECTION: extract_amount_correction,
    FactKind.TRANSACTION_TREATMENT: extract_treatment,
}


def extract_candidates(
    requirement: FactRequirement,
    document: CanonicalDocument,
    *,
    authority_domain: AuthorityDomain | None = None,
) -> list[FactCandidate]:
    """Run the deterministic extractor for the requirement's FactKind."""
    fn = _EXTRACTORS.get(requirement.fact_kind)
    if fn is None:
        return []
    domain = authority_domain or (
        requirement.allowed_authority_domains[0]
        if requirement.allowed_authority_domains
        else AuthorityDomain.FINANCIAL_ADJUSTMENTS
    )
    return fn(requirement, document, domain)


# Silence unused import lint when TXN_ID_RE only used indirectly in some builds.
_ = TXN_ID_RE
