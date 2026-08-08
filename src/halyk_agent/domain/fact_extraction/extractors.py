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
    GroupCapexDerivationType,
    GroupCapexPayload,
    MoneyAmount,
    OffLedgerAmountPayload,
    OneTimeAddBackPayload,
    OwnershipPayload,
    PeriodDisposition,
    RateSource,
    ReclassificationDisposition,
    RelatedPartyThresholdPayload,
    SubsidiaryDerivationType,
    SubsidiaryKind,
    SubsidiaryStatusPayload,
    TransactionPeriodPayload,
    TransactionReclassificationPayload,
    TransactionTreatmentPayload,
    TreatmentDisposition,
)
from halyk_agent.domain.fact_extraction.ownership_context import ownership_context_reason
from halyk_agent.domain.fact_extraction.text_locate import (
    TXN_ID_RE,
    find_txn_ids,
    page_text_slices,
    parse_money,
    parse_percentage,
)
from halyk_agent.domain.fact_extraction.text_normalize import cue_corpus
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

# Specific proposal/review + rejection / original-remains (NOT generic "не требовалось").
_REJECTED_RECLASS_RU = re.compile(
    r"(?P<body>"
    r"(?:Операция\s+)?(?P<txn>TXN-[A-Za-z0-9]+-\d+)"
    r"[^.\n]{0,220}?"
    r"(?:первоначально\s+учт[её]нн\w*\s+как\s+(?P<from>[^,(.\n]{2,80}?))?"
    r"(?:\s*\((?P<money>\$[\d,]+(?:\.\d{2})?)\))?"
    r"[^.\n]{0,200}?"
    r"(?:"
    r"рассматрива\w*.{0,80}?(?:перекласс\w*|переквалиф\w*)"
    r"|предлага\w*.{0,60}?(?:отнести|перекласс\w*|переквалиф\w*)"
    r"|на\s+предмет\s+возможн\w*\s+(?:перекласс\w*|переквалиф\w*)"
    r")"
    r"(?:.{0,80}?как\s+(?P<to>[A-Za-zА-Яа-яЁё][\w\s\-]{1,60}?)"
    r"(?=\s*;|\s*по\s+итогам|\s*,\s*по\s+итогам))?"
    r".{0,260}?"
    r"(?:"
    r"первоначальн\w*\s+классификац\w*.{0,40}?сохраня"
    r"|корректировк\w*.{0,40}?(?:не\s+производи|не\s+требуется|не\s+требует)"
    r"|предложен\w*.{0,40}?отклонен"
    r"|рассмотрен\w*.{0,40}?отклонен"
    r")"
    r")",
    re.IGNORECASE | re.DOTALL,
)

_REJECTED_RECLASS_RU_ADJ = re.compile(
    r"(?P<body>"
    r"(?:Операция\s+)?(?P<txn>TXN-[A-Za-z0-9]+-\d+)"
    r"(?:\s*\((?P<money>\$[\d,]+(?:\.\d{2})?)[^)]*\))?"
    r"[^.\n]{0,200}?"
    r"(?:"
    r"(?:запрошен\w*|проверена|рассмотрен\w*|по\s+результатам\s+рассмотрения)"
    r".{0,120}?"
    r")?"
    r"(?:"
    r"корректировк\w*.{0,60}?(?:не\s+требуется|не\s+требует|не\s+производи)"
    r"|первоначальн\w*\s+классификац\w*.{0,40}?сохраня"
    r")"
    r"(?:.{0,120}?"
    r"(?:первоначальн\w*\s+классификац\w*.{0,40}?сохраня"
    r"|корректировк\w*.{0,40}?(?:не\s+требуется|не\s+требует|не\s+производи))"
    r")?"
    r")",
    re.IGNORECASE | re.DOTALL,
)

_REJECTED_RECLASS_EN = re.compile(
    r"(?P<body>"
    r"(?:Transaction\s+)?(?P<txn>TXN-[A-Za-z0-9]+-\d+)"
    r"[^.\n]{0,200}?"
    r"(?:\((?P<money>\$[\d,]+(?:\.\d{2})?)\))?"
    r"[^.\n]{0,160}?"
    r"(?:"
    r"considered\s+for\s+reclass\w*"
    r"|proposed\s+reclass\w*"
    r"|reviewed\s+(?:at\s+lender\s+request|for\s+reclass\w*)"
    r")"
    r"(?:.{0,80}?(?:to|as)\s+(?P<to>[^;,\n.]{2,80}))?"
    r"(?:.{0,80}?(?:from|originally\s+(?:recorded|classified)\s+as)\s+"
    r"(?P<from>[^;,\n.]{2,80}))?"
    r".{0,220}?"
    r"(?:"
    r"original\s+classification\s+(?:remains|retained)"
    r"|adjustment\s+(?:was\s+)?not\s+(?:made|required)"
    r"|proposal\s+rejected"
    r"|considered\s+and\s+rejected"
    r")"
    r")",
    re.IGNORECASE | re.DOTALL,
)


def _clean_category(raw: str | None) -> str | None:
    """Normalize captured category text (stop at clause/purpose connectors)."""
    if raw is None:
        return None
    text = re.sub(r"\s+", " ", raw).strip(" ,.;:\n\t()")
    if not text:
        return None
    text = re.split(
        r"\s+(?:для\s+целей|был|была|были|was|were|for\s+covenant|Основание|"
        r"рассматрива|proposed|considered|по\s+итогам|;)\b",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    # Stop before trailing clause markers that may remain after newline joins.
    text = re.split(r"\s*;\s*", text, maxsplit=1)[0]
    cleaned = text.strip(" ,.;:\n\t()")
    return cleaned or None


def _joined_document_text(
    document: CanonicalDocument,
) -> tuple[str, list[tuple[int, int, str]]]:
    """Join pages with newlines; return (joined, [(global_start, page_number, page_text)])."""
    slices = page_text_slices(document)
    parts: list[str] = []
    index: list[tuple[int, int, str]] = []
    offset = 0
    for page_number, text in slices:
        index.append((offset, page_number, text))
        parts.append(text)
        offset += len(text) + 1
    return "\n".join(parts), index


def _map_joined_offset(global_offset: int, index: list[tuple[int, int, str]]) -> tuple[int, int]:
    """Map joined-text offset → (page_number, page_local_offset)."""
    for start, page_number, text in index:
        end = start + len(text)
        if start <= global_offset <= end:
            return page_number, global_offset - start
        if global_offset == end + 1:
            # Boundary newline — attribute to this page end.
            return page_number, len(text)
    if index:
        start, page_number, text = index[-1]
        return page_number, min(len(text), max(0, global_offset - start))
    return 1, 0


def extract_reclassification(
    requirement: FactRequirement,
    document: CanonicalDocument,
    authority_domain: AuthorityDomain,
) -> list[FactCandidate]:
    out: list[FactCandidate] = []
    seen_keys: set[str] = set()
    # Join pages so proposal/rejection sentences that cross page breaks still match.
    text, page_index = _joined_document_text(document)

    def _emit(
        match: re.Match[str],
        *,
        disposition: ReclassificationDisposition,
        reason_code: str,
        from_cat: str | None,
        to_cat: str | None,
        money_raw: str | None,
        txn_id: str | None,
        counterparty: str | None = None,
    ) -> None:
        if disposition is ReclassificationDisposition.ACCEPTED:
            dedupe = f"{txn_id}|{from_cat}|{to_cat}|ACCEPTED|{money_raw or ''}"
        else:
            dedupe = f"{txn_id}|{from_cat}|{to_cat}|REJECTED|{money_raw or ''}"
        if dedupe in seen_keys:
            return
        g_start = match.start("body")
        g_end = match.end("body")
        page_number, local_start = _map_joined_offset(g_start, page_index)
        # Quote must be an exact substring of a single page for evidence alignment.
        page_text = next((t for s, p, t in page_index if p == page_number), "")
        local_end = min(len(page_text), local_start + (g_end - g_start))
        # If the match spans pages, prefer the page that contains the TXN id / money.
        quote = page_text[local_start:local_end]
        if txn_id and txn_id not in quote:
            for _start, pnum, ptext in page_index:
                if txn_id in ptext:
                    idx = ptext.find(txn_id)
                    # Take a bounded statement window on that page.
                    q0 = max(0, ptext.rfind("\n", 0, idx) + 1)
                    q1 = ptext.find("\n\n", idx)
                    if q1 < 0:
                        q1 = min(len(ptext), idx + 500)
                    # Extend to cover rejection cues still on this page.
                    page_number = pnum
                    local_start = q0
                    local_end = q1
                    quote = ptext[local_start:local_end].strip()
                    local_start = ptext.find(quote)
                    local_end = local_start + len(quote)
                    break
        # Evidence must cover stated amount/categories when present on the quote page.
        if money_raw and money_raw not in quote:
            for _start, pnum, ptext in page_index:
                if money_raw in ptext and (txn_id is None or txn_id in ptext):
                    idx = ptext.find(txn_id or money_raw)
                    q0 = max(0, idx - 40)
                    q1 = min(len(ptext), idx + 420)
                    snippet = ptext[q0:q1]
                    page_number = pnum
                    local_start = q0
                    local_end = q1
                    quote = snippet
                    break
        try:
            amount = None
            if money_raw:
                money = parse_money(money_raw)
                if money is not None:
                    amount = MoneyAmount(value=money[0], currency=money[1])
            if disposition is ReclassificationDisposition.ACCEPTED:
                if not from_cat or not to_cat:
                    return
            elif not any((txn_id, amount, from_cat, to_cat)):
                return
            payload = TransactionReclassificationPayload(
                transaction_id=txn_id,
                counterparty=counterparty,
                amount=amount,
                from_category=from_cat,
                to_category=to_cat,
                disposition=disposition,
            )
        except Exception:
            return
        seen_keys.add(dedupe)
        out.append(
            _candidate(
                requirement=requirement,
                document=document,
                authority_domain=authority_domain,
                kind=FactKind.TRANSACTION_RECLASSIFICATION,
                payload=payload,
                quote=quote if quote.strip() else match.group("body")[:500],
                page_number=page_number,
                char_start=local_start,
                char_end=local_end if local_end > local_start else local_start + len(quote),
                reason_code=reason_code,
            )
        )

    for pattern in (_RECLASS_RU, _RECLASS_EN):
        for match in pattern.finditer(text):
            money = parse_money(match.group("money"))
            if money is None:
                continue
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
            txn_ids = find_txn_ids(match.group("body"))
            _emit(
                match,
                disposition=disposition,
                reason_code="DET_RECLASS",
                from_cat=from_cat,
                to_cat=to_cat,
                money_raw=match.group("money"),
                txn_id=txn_ids[0] if txn_ids else None,
                counterparty=cp or None,
            )

    for pattern, reason in (
        (_REJECTED_RECLASS_RU, "DET_RECLASS_REJECTED"),
        (_REJECTED_RECLASS_RU_ADJ, "DET_RECLASS_REJECTED_ADJ"),
        (_REJECTED_RECLASS_EN, "DET_RECLASS_REJECTED_EN"),
    ):
        for match in pattern.finditer(text):
            groups = match.groupdict()
            txn_id = groups.get("txn")
            from_cat = _clean_category(groups.get("from"))
            to_cat = _clean_category(groups.get("to"))
            if from_cat is None:
                restated = re.search(
                    r"первоначальн\w*\s+классификац\w*\s*\((?P<fc>[^)]{2,80})\)",
                    match.group("body"),
                    re.IGNORECASE,
                )
                if restated:
                    from_cat = _clean_category(restated.group("fc"))
            money_raw = groups.get("money")
            if money_raw is None:
                money_hit = re.search(r"\$[\d,]+(?:\.\d{2})?", match.group("body"))
                money_raw = money_hit.group(0) if money_hit else None
            # Skip generic absence statements (CONFIRMED_NONE territory).
            body_fold = match.group("body").casefold()
            if (
                re.search(
                    r"переклассификац\w*\s+(?:за\s+ковенантн\w*\s+период\w*\s+)?не\s+требовал",
                    body_fold,
                )
                and "рассматрива" not in body_fold
                and "предлага" not in body_fold
            ):
                continue
            _emit(
                match,
                disposition=ReclassificationDisposition.REJECTED,
                reason_code=reason,
                from_cat=from_cat,
                to_cat=to_cat,
                money_raw=money_raw,
                txn_id=txn_id,
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


def _period_evidence_span(
    text: str, match: re.Match[str], start: object, end: object
) -> tuple[str, int, int]:
    """Expand quote so service_start/service_end dates are covered when present nearby."""
    body_start = match.start("body")
    body_end = match.end("body")
    if start is not None and end is not None:
        window_start = max(0, match.start() - 80)
        window = text[window_start : min(len(text), match.end() + 160)]
        date_match = _SERVICE_RANGE.search(window)
        if date_match is not None:
            abs_start = window_start + date_match.start()
            abs_end = window_start + date_match.end()
            q0 = min(body_start, abs_start)
            q1 = max(body_end, abs_end)
            return text[q0:q1], q0, q1
    return text[body_start:body_end], body_start, body_end


def extract_period(
    requirement: FactRequirement,
    document: CanonicalDocument,
    authority_domain: AuthorityDomain,
) -> list[FactCandidate]:
    out: list[FactCandidate] = []
    for page_number, text in page_text_slices(document):
        for match in _PERIOD_EXCLUDE.finditer(text):
            label = (match.group("label") or "").strip(" ,.;") or None
            start, end = _period_dates(text, match.start(), match.end())
            quote, q0, q1 = _period_evidence_span(text, match, start, end)
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
                    quote=quote[:500],
                    page_number=page_number,
                    char_start=q0,
                    char_end=q1,
                    reason_code="DET_PERIOD_EXCLUDE",
                )
            )
        for match in _PERIOD_ASSIGN.finditer(text):
            label = (match.group("label") or "").strip(" ,.;") or None
            start, end = _period_dates(text, match.start(), match.end())
            if start and end and not label:
                label = f"{start.isoformat()}..{end.isoformat()}"
            quote, q0, q1 = _period_evidence_span(text, match, start, end)
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
                    quote=quote[:500],
                    page_number=page_number,
                    char_start=q0,
                    char_end=q1,
                    reason_code="DET_PERIOD_ASSIGN",
                )
            )
    return out


_LEGAL_FORM = r"(?:LLP|JSC|Inc\.?|LLC|Ltd\.?|PLC|ТОО|АО|ООО|TOO)"
# Line-local rows only — do not let \\s bridge a header line into "LLP 37.5%".
_OWNERSHIP_ROW = re.compile(
    r"(?P<body>"
    r"(?:"
    r"[\"«„](?P<qname>[^\"»“\n]{2,80}?)[\"»“][ \t]*(?P<qform>" + _LEGAL_FORM + r")"
    r"|"
    r"[\"«„](?P<qname2>[^\"»“\n]{2,80}?)[\"»“]"
    r"|"
    r"(?P<entity>[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9 .,&'\-]{0,80}?)"
    r"(?:[ \t]+(?P<form>" + _LEGAL_FORM + r"))?"
    r")"
    r"[ \t]+(?P<pct>\d+(?:[.,]\d+)?)\s*%"
    r")",
    re.UNICODE,
)


def _ownership_entity_name(match: re.Match[str]) -> str | None:
    qname = match.groupdict().get("qname") or match.groupdict().get("qname2")
    if qname:
        form = match.groupdict().get("qform")
        name = qname.strip(" ,.;:\t")
        if form:
            return f"{name} {form.strip()}"
        return name
    entity = (match.groupdict().get("entity") or "").strip(" :-\t,")
    form = match.groupdict().get("form")
    if not entity:
        return None
    # If entity already ends with legal form, keep as-is.
    if form and not re.search(_LEGAL_FORM + r"\s*$", entity, re.IGNORECASE):
        return f"{entity} {form.strip()}"
    return entity


def extract_ownership(
    requirement: FactRequirement,
    document: CanonicalDocument,
    authority_domain: AuthorityDomain,
) -> list[FactCandidate]:
    """
    Emit ownership rows only when local preceding table/section context is
    ownership/voting-rights (not pledged-assets / collateral tables).
    """
    out: list[FactCandidate] = []
    for page_number, text in page_text_slices(document):
        for match in _OWNERSHIP_ROW.finditer(text):
            context_reason = ownership_context_reason(text, match.start())
            if context_reason is None:
                continue
            entity = _ownership_entity_name(match)
            if not entity:
                continue
            # Skip table headers / boilerplate.
            if entity.casefold() in {
                "организация",
                "entity",
                "организация доля голосующих прав",
                "дочерняя организация",
            }:
                continue
            if "доля" in entity.casefold() and len(entity) < 40:
                continue
            if not is_meaningful_entity_name(entity):
                continue
            pct = parse_percentage(match.group("pct") + "%")
            if pct is None:
                continue
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
                    reason_code=f"DET_{context_reason}",
                )
            )
    return out


_RP_THRESHOLD = re.compile(
    r"(?P<body>(?:владеет|owns)\s+(?P<pct>\d+(?:[.,]\d+)?)\s*%\s+"
    r"(?:и\s+более|or\s+more|или\s+более)[^.]{0,100}?"
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
        repaired = cue_corpus(text)
        search_text = repaired if repaired != text else text
        for match in _RP_THRESHOLD.finditer(search_text):
            pct = parse_percentage(match.group("pct") + "%")
            if pct is None:
                continue
            token = f"{match.group('pct')}%"
            # Quote must be an exact substring of the original page text.
            idx = text.find(token)
            if idx < 0:
                alt = token.replace(".", ",")
                idx = text.find(alt)
                token = alt if idx >= 0 else token
            if idx < 0:
                continue
            line_start = text.rfind("\n", 0, idx) + 1
            line_end = text.find("\n", idx)
            if line_end < 0:
                line_end = len(text)
            # Include following line when related-party wording wraps.
            next_end = text.find("\n", line_end + 1)
            if next_end < 0:
                next_end = min(len(text), line_end + 200)
            quote = text[line_start:next_end].strip()
            q0 = text.find(quote, line_start)
            if q0 < 0:
                q0 = line_start
                quote = text[line_start:line_end]
            q1 = q0 + len(quote)
            payload = RelatedPartyThresholdPayload(threshold_percent=pct)
            out.append(
                _candidate(
                    requirement=requirement,
                    document=document,
                    authority_domain=authority_domain,
                    kind=FactKind.RELATED_PARTY_THRESHOLD,
                    payload=payload,
                    quote=quote[:400],
                    page_number=page_number,
                    char_start=q0,
                    char_end=q1,
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
SEVERANCE_AS_OF_RE = re.compile(
    r"(?:действующ\w*\s+на|по\s+состоянию\s+на|as\s+of|as-at)\s*"
    r"(?P<as_of>20\d{2}-\d{2}-\d{2})",
    re.IGNORECASE,
)


def _severance_as_of_in_text(text: str, *, around_start: int, around_end: int) -> date | None:
    """Parse source-backed AS_OF near a severance mention; None means undecidable."""
    window = text[max(0, around_start - 220) : min(len(text), around_end + 220)]
    local = SEVERANCE_AS_OF_RE.search(window)
    if local is not None:
        return _parse_iso_date(local.group("as_of"))
    # Document-level fallback only when a single unambiguous as-of accompanies severance language.
    if not re.search(r"выходн\w*\s+пособи|severance", text, re.IGNORECASE):
        return None
    hits = list(SEVERANCE_AS_OF_RE.finditer(text))
    dates = {_parse_iso_date(m.group("as_of")) for m in hits}
    dates.discard(None)
    if len(dates) == 1:
        return next(iter(dates))
    return None


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
            as_of = _severance_as_of_in_text(
                text, around_start=match.start(), around_end=match.end()
            )
            quote = body
            q0, q1 = match.start(), match.end()
            if as_of is not None:
                # Expand evidence so the AS_OF date token is covered when present nearby.
                as_of_m = SEVERANCE_AS_OF_RE.search(
                    text[max(0, match.start() - 220) : min(len(text), match.end() + 220)]
                )
                if as_of_m is not None:
                    abs0 = max(0, match.start() - 220) + as_of_m.start()
                    abs1 = max(0, match.start() - 220) + as_of_m.end()
                    q0 = min(match.start(), abs0)
                    q1 = max(match.end(), abs1)
                    quote = text[q0:q1]
            payload = OffLedgerAmountPayload(
                label="severance_liability",
                amount=MoneyAmount(value=money[0], currency=money[1]),
                as_of_date=as_of,
            )
            out.append(
                _candidate(
                    requirement=requirement,
                    document=document,
                    authority_domain=authority_domain,
                    kind=FactKind.OFF_LEDGER_AMOUNT,
                    payload=payload,
                    quote=quote[:400],
                    page_number=page_number,
                    char_start=q0,
                    char_end=q1,
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


_SECURITY_PERIMETER_RULE = re.compile(
    r"(?:доля\s+активов\s+в\s+залоге|pledged\s+assets?[^\n%]{0,40})"
    r"[^\n.]{0,120}?(?:ниже|below|less\s+than)\s+"
    r"(?P<thr>\d+(?:[.,]\d+)?)\s*%"
    r"[^.]{0,220}?(?:неограниченн\w*|unrestricted)",
    re.IGNORECASE | re.DOTALL,
)

_PLEDGED_ASSET_ROW = re.compile(
    r"(?P<entity>[A-Za-z][\w .,&'\-]{2,80}?(?:LLP|JSC|TOO|Inc|АО|ТОО))"
    r"\s+(?P<pct>\d+(?:[.,]\d+)?)\s*%",
    re.IGNORECASE,
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
            payload = SubsidiaryStatusPayload(
                entity_name=entity,
                status=status,
                derivation_type=SubsidiaryDerivationType.DIRECT_QUOTE,
            )
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

        # Security-perimeter threshold: pledged-assets % vs rule → RESTRICTED/UNRESTRICTED.
        rule = _SECURITY_PERIMETER_RULE.search(text)
        if rule is None:
            continue
        try:
            thr = Decimal(rule.group("thr").replace(",", "."))
        except Exception:
            continue
        if thr <= 0 or thr > Decimal("100"):
            continue
        rule_span = (rule.start(), rule.end())
        for row in _PLEDGED_ASSET_ROW.finditer(text):
            entity = re.sub(r"\s+", " ", row.group("entity")).strip(" ,.;")
            if not is_meaningful_entity_name(entity):
                continue
            try:
                pct = Decimal(row.group("pct").replace(",", "."))
            except Exception:
                continue
            status = SubsidiaryKind.UNRESTRICTED if pct < thr else SubsidiaryKind.RESTRICTED
            # Evidence quote covers row + rule (source-faithful derivation components).
            start = min(row.start(), rule_span[0])
            end = max(row.end(), rule_span[1])
            quote = text[start:end][:800]
            payload = SubsidiaryStatusPayload(
                entity_name=entity,
                status=status,
                derivation_type=SubsidiaryDerivationType.SECURITY_PERIMETER_THRESHOLD,
                observed_percentage=pct,
                threshold_percentage=thr,
                comparator="LT",
            )
            out.append(
                _candidate(
                    requirement=requirement,
                    document=document,
                    authority_domain=authority_domain,
                    kind=FactKind.SUBSIDIARY_STATUS,
                    payload=payload,
                    quote=quote,
                    page_number=page_number,
                    char_start=start,
                    char_end=end,
                    reason_code="DET_SUBSIDIARY_SECURITY_PERIMETER",
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

_ONE_TIME_SECTION = re.compile(
    r"(?:разовые\s+статьи|разовыми\s+для\s+целей|единовременн\w*|"
    r"one[-\s]?time|add[-\s]?back|корректировк\w*\s+EBITDA)",
    re.IGNORECASE,
)

_ONE_TIME_ROW = re.compile(
    # Allow a single soft line-break inside the label (source tables wrap).
    r"(?P<label>[^\n$]{3,120}?(?:\n[^\n«\"“'$]{1,80})?)"
    r"(?:«|\"|“|'|)?"
    r"(?P<counterparty>[A-Za-zА-Яа-яЁёІі][\w .,&'!\-]{1,80}?"
    r"(?:LLP|JSC|TOO|Inc|АО|ТОО|Bureau|Associates))"
    r"(?:»|\"|”|'|)?"
    r"[^\n$]{0,40}?"
    r"(?P<money>\$[\d,]+(?:\.\d{2})?)",
    re.IGNORECASE,
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

        # Table-style one-time items (do NOT apply materiality floor here).
        if not _ONE_TIME_SECTION.search(text):
            continue
        for match in _ONE_TIME_ROW.finditer(text):
            money = parse_money(match.group("money"))
            if money is None:
                continue
            # Skip the materiality threshold sentence itself.
            label = re.sub(r"\s+", " ", match.group("label")).strip(" =;:-")
            if re.search(r"не\s+менее|not\s+less|materiality|порог", label, re.IGNORECASE):
                continue
            if len(label) < 5:
                continue
            counterparty = re.sub(r"\s+", " ", match.group("counterparty")).strip(" ,.;")
            body = text[match.start() : match.end()].strip()
            payload = OneTimeAddBackPayload(
                label=label[:200],
                amount=MoneyAmount(value=money[0], currency=money[1]),
                counterparty=counterparty or None,
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
                    reason_code="DET_ONE_TIME_TABLE_ROW",
                )
            )
    return out


_PPE_OPENING = re.compile(
    r"net\s+book\s+value\s+at\s+the\s+beginning\s+of\s+the\s+year\s*"
    r"(?P<money>\$[\d,]+(?:\.\d{2})?)",
    re.IGNORECASE,
)
_PPE_DEPR = re.compile(
    r"depreciation\s+charge\s+for\s+the\s+year\s*"
    r"(?P<money>\$[\d,]+(?:\.\d{2})?)",
    re.IGNORECASE,
)
_PPE_CLOSING = re.compile(
    r"net\s+book\s+value\s+at\s+the\s+end\s+of\s+the\s+year\s*"
    r"(?P<money>\$[\d,]+(?:\.\d{2})?)",
    re.IGNORECASE,
)
_PPE_ADDITIONS = re.compile(
    r"(?:additions|capital\s+expenditure|capex)\s*"
    r"(?P<money>\$[\d,]+(?:\.\d{2})?)",
    re.IGNORECASE,
)

# Movement families that must be independently known (value or zero-proven) before
# solving additions from opening/closing/depreciation alone.
_PPE_REQUIRED_ZERO_FAMILIES: tuple[str, ...] = (
    "disposals",
    "acquisitions",
    "transfers",
    "fx",
    "impairment",
    "revaluation",
)

_PPE_FAMILY_ALIASES: dict[str, tuple[str, ...]] = {
    "disposals": ("disposal",),
    "acquisitions": ("acquisition", "business combination"),
    "transfers": ("transfer",),
    "fx": ("foreign exchange", "fx", "translation"),
    "impairment": ("impairment",),
    "revaluation": ("revaluation",),
    "other": ("other movement",),
}

_PPE_NO_OTHER_MOVEMENTS = re.compile(r"\bno\s+other\s+movements\b", re.IGNORECASE)
_PPE_THERE_WERE_NO = re.compile(
    r"there\s+were\s+no\s+([^.!\n]+)",
    re.IGNORECASE,
)


def _ppe_zero_proven_families(text: str) -> set[str]:
    """Return movement families explicitly proven zero in source text."""
    proven: set[str] = set()
    if _PPE_NO_OTHER_MOVEMENTS.search(text):
        # Explicit catch-all may close remaining non-addition movements.
        proven.update(_PPE_REQUIRED_ZERO_FAMILIES)
        proven.add("other")
        return proven

    lowered = text.casefold()
    for family, aliases in _PPE_FAMILY_ALIASES.items():
        if family == "other":
            continue
        # Standalone "no <family>" (not via unsafe OR wildcard).
        for alias in aliases:
            if re.search(rf"\bno\s+{re.escape(alias)}s?\b", lowered):
                proven.add(family)
                break

    for match in _PPE_THERE_WERE_NO.finditer(text):
        chunk = match.group(1).casefold()
        for family, aliases in _PPE_FAMILY_ALIASES.items():
            if family == "other":
                continue
            if any(alias in chunk for alias in aliases):
                proven.add(family)
    return proven


def ppe_roll_forward_is_closed(text: str) -> bool:
    """
    True only when every non-addition movement family is proven zero/known.

    'There were no disposals' proves disposals only — never all movements.
    'There were no other movements' may close the remaining families.
    """
    proven = _ppe_zero_proven_families(text)
    return set(_PPE_REQUIRED_ZERO_FAMILIES).issubset(proven)


def has_incomplete_ppe_roll_forward(document: CanonicalDocument) -> bool:
    """True when a PPE bridge shows opening/depr/closing but is not closed for CAPEX."""
    for _page_number, text in page_text_slices(document):
        if not (_PPE_OPENING.search(text) and _PPE_DEPR.search(text) and _PPE_CLOSING.search(text)):
            continue
        if _PPE_ADDITIONS.search(text):
            continue
        if ppe_roll_forward_is_closed(text):
            continue
        return True
    return False


def extract_group_capex(
    requirement: FactRequirement,
    document: CanonicalDocument,
    authority_domain: AuthorityDomain,
) -> list[FactCandidate]:
    """
    Derive GROUP_CAPEX only from an explicit amount or a closed PPE roll-forward.

    Incomplete bridges (missing additions / unproven other movements) yield nothing.
    """
    out: list[FactCandidate] = []
    for page_number, text in page_text_slices(document):
        # Explicit additions line preferred.
        for match in _PPE_ADDITIONS.finditer(text):
            money = parse_money(match.group("money"))
            if money is None or money[0] <= 0:
                continue
            # Require PPE / NBV context on the page so segment CAPEX isn't grabbed blindly.
            if not re.search(r"net\s+book\s+value|property,\s*plant", text, re.IGNORECASE):
                continue
            payload = GroupCapexPayload(
                amount=MoneyAmount(value=money[0], currency=money[1]),
                derivation_type=GroupCapexDerivationType.EXPLICIT,
                additions_amount=MoneyAmount(value=money[0], currency=money[1]),
                formula="EXPLICIT_ADDITIONS",
            )
            out.append(
                _candidate(
                    requirement=requirement,
                    document=document,
                    authority_domain=authority_domain,
                    kind=FactKind.GROUP_CAPEX,
                    payload=payload,
                    quote=match.group(0)[:400],
                    page_number=page_number,
                    char_start=match.start(),
                    char_end=match.end(),
                    reason_code="DET_GROUP_CAPEX_EXPLICIT",
                )
            )

        opening_m = _PPE_OPENING.search(text)
        depr_m = _PPE_DEPR.search(text)
        closing_m = _PPE_CLOSING.search(text)
        additions_m = _PPE_ADDITIONS.search(text)
        if not (opening_m and depr_m and closing_m):
            continue
        opening = parse_money(opening_m.group("money"))
        depr = parse_money(depr_m.group("money"))
        closing = parse_money(closing_m.group("money"))
        if opening is None or depr is None or closing is None:
            continue
        if additions_m is not None:
            # Explicit additions already emitted above when present.
            continue
        # Derive only when every non-addition movement is independently proven.
        if not ppe_roll_forward_is_closed(text):
            continue
        additions_val = closing[0] - opening[0] + depr[0]
        if additions_val <= 0:
            continue
        start = min(opening_m.start(), depr_m.start(), closing_m.start())
        end = max(opening_m.end(), depr_m.end(), closing_m.end())
        payload = GroupCapexPayload(
            amount=MoneyAmount(value=additions_val, currency=closing[1]),
            derivation_type=GroupCapexDerivationType.PPE_ROLL_FORWARD,
            opening_amount=MoneyAmount(value=opening[0], currency=opening[1]),
            depreciation_amount=MoneyAmount(value=depr[0], currency=depr[1]),
            closing_amount=MoneyAmount(value=closing[0], currency=closing[1]),
            additions_amount=MoneyAmount(value=additions_val, currency=closing[1]),
            formula="closing - opening + depreciation",
            other_movements_proven_zero=True,
        )
        out.append(
            _candidate(
                requirement=requirement,
                document=document,
                authority_domain=authority_domain,
                kind=FactKind.GROUP_CAPEX,
                payload=payload,
                quote=text[start:end][:600],
                page_number=page_number,
                char_start=start,
                char_end=end,
                reason_code="DET_GROUP_CAPEX_PPE_ROLL_FORWARD",
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
    FactKind.GROUP_CAPEX: extract_group_capex,
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
