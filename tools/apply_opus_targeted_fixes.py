"""Apply the confirmed Opus destructive-red-team BLOCKER/HIGH fixes.

Temporary branch-only helper.  It performs exact source replacements so the fix
can be reproduced by GitHub Actions without granting an auditor write access to
production code.  Delete this helper before merging the fix PR.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _path(rel: str) -> Path:
    return ROOT / rel


def _read(rel: str) -> str:
    return _path(rel).read_text(encoding="utf-8")


def _write(rel: str, text: str) -> None:
    _path(rel).parent.mkdir(parents=True, exist_ok=True)
    _path(rel).write_text(text, encoding="utf-8", newline="\n")


def replace_once(rel: str, old: str, new: str) -> None:
    text = _read(rel)
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{rel}: expected exactly one replacement, found {count}: {old[:80]!r}")
    _write(rel, text.replace(old, new, 1))


def replace_count(rel: str, old: str, new: str, expected: int) -> None:
    text = _read(rel)
    count = text.count(old)
    if count != expected:
        raise RuntimeError(f"{rel}: expected {expected} replacements, found {count}: {old[:80]!r}")
    _write(rel, text.replace(old, new))


def replace_between(rel: str, start: str, end: str, replacement: str) -> None:
    text = _read(rel)
    i = text.find(start)
    if i < 0:
        raise RuntimeError(f"{rel}: start marker not found: {start!r}")
    j = text.find(end, i + len(start))
    if j < 0:
        raise RuntimeError(f"{rel}: end marker not found: {end!r}")
    _write(rel, text[:i] + replacement + text[j:])


# ---------------------------------------------------------------------------
# BLOCKER-1 + HIGH-1/HIGH-2: one fail-closed money/locale contract.
# ---------------------------------------------------------------------------

parse_rel = "src/halyk_agent/domain/covenants/parse.py"
replace_once(
    parse_rel,
    '_MONEY_PREFIX_RE = re.compile(r"(?P<prefix>\\$|USD\\s+|EUR\\s+|€|KZT\\s+|₸)\\s*", re.IGNORECASE)\n',
    '_MONEY_PREFIX_RE = re.compile(\n'
    '    r"(?P<prefix>\\$|USD\\s+|EUR\\s+|€|KZT\\s+|₸|GBP\\s+|£|RUB\\s+|JPY\\s+|¥)\\s*",\n'
    '    re.IGNORECASE,\n'
    ')\n',
)
replace_once(
    parse_rel,
    '_RATIO_TOKEN_RE = re.compile(\n    r"(?<![0-9.])(?P<num>\\d+(?:\\.\\d+)?)\\s*x\\b(?!\\w)",\n    re.IGNORECASE,\n)\n_PERCENT_TOKEN_RE = re.compile(\n    r"(?<![0-9.])(?P<num>\\d+(?:\\.\\d+)?)\\s*%(?!\\w)",\n)\n_MALFORMED_RATIO_RE = re.compile(r"(?<![0-9])\\d+(?:\\.\\d+){2,}\\s*x\\b", re.IGNORECASE)\n',
    '_RATIO_TOKEN_RE = re.compile(\n    r"(?<![0-9.,])(?P<num>\\d+(?:[.,]\\d+)?)\\s*x\\b(?!\\w)",\n    re.IGNORECASE,\n)\n_PERCENT_TOKEN_RE = re.compile(\n    r"(?<![0-9.,])(?P<num>\\d+(?:[.,]\\d+)?)\\s*%(?!\\w)",\n)\n_MALFORMED_RATIO_RE = re.compile(\n    r"(?<![0-9])\\d+(?:[.,]\\d+){2,}\\s*x\\b", re.IGNORECASE\n)\n',
)
replace_once(
    parse_rel,
    '    if token in {"₸", "KZT"}:\n        return "KZT"\n    return None\n',
    '    if token in {"₸", "KZT"}:\n        return "KZT"\n'
    '    if token in {"£", "GBP"}:\n        return "GBP"\n'
    '    if token == "RUB":\n        return "RUB"\n'
    '    if token in {"¥", "JPY"}:\n        return "JPY"\n'
    '    return None\n',
)
replace_once(
    parse_rel,
    'def _norm(text: str) -> str:\n    return " ".join(text.replace("\\xa0", " ").split())\n\n\n',
    'def _norm(text: str) -> str:\n    return " ".join(text.replace("\\xa0", " ").split())\n\n\n'
    'def _coerce_threshold_number(raw: str):\n'
    '    """Parse a dot/comma decimal threshold without permitting partial matches."""\n\n'
    '    return coerce_decimal_amount(raw.replace(",", "."))\n\n\n',
)
replace_count(
    parse_rel,
    'value = coerce_decimal_amount(match.group("num"))',
    'value = _coerce_threshold_number(match.group("num"))',
    2,
)
replace_once(
    parse_rel,
    '            nxt = i + 1\n            if nxt < n and _is_money_group_space(rest[nxt]):\n',
    '            nxt = i + 1\n'
    '            if nxt < n and rest[nxt] == ",":\n'
    '                return _MoneyNumericParse(\n'
    '                    "", _consume_malformed_money_tail(rest, 0), False\n'
    '                )\n'
    '            if nxt < n and _is_money_group_space(rest[nxt]):\n',
)

fact_rel = "src/halyk_agent/domain/fact_extraction/text_locate.py"
replace_once(
    fact_rel,
    'from halyk_agent.domain.parsing import CanonicalDocument\n',
    'from halyk_agent.domain.covenants.parse import scan_money_quantities\n'
    'from halyk_agent.domain.parsing import CanonicalDocument\n',
)
replace_once(
    fact_rel,
    '_SYM_CURRENCY = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}\n',
    '_SYM_CURRENCY = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}\n'
    '_SUFFIX_MONEY_RE = re.compile(\n'
    '    r"(?<![\\w,.\'’`])"\n'
    '    r"(?P<num>(?:\\d{1,3}(?:[ \\xa0\\u202f\\u2009]\\d{3})+|\\d{1,3}(?:,\\d{3})+|\\d+)"\n'
    '    r"(?:[.,]\\d{1,2})?)\\s*(?P<code>USD|EUR|GBP|KZT|RUB|JPY)\\b",\n'
    '    re.IGNORECASE,\n'
    ')\n',
)
replace_once(
    fact_rel,
    '    cleaned = raw.strip().replace("\\xa0", "").replace(" ", "")\n',
    '    cleaned = raw.strip()\n'
    '    for separator in ("\\xa0", "\\u202f", "\\u2009", " ", "\\t"):\n'
    '        cleaned = cleaned.replace(separator, "")\n',
)
replace_between(
    fact_rel,
    'def parse_money(text: str) -> tuple[Decimal, str] | None:\n',
    'def parse_percentage(text: str) -> Decimal | None:\n',
    '''def parse_money(text: str) -> tuple[Decimal, str] | None:
    """Parse the first complete money token, failing closed on malformed prefixes."""

    scan = scan_money_quantities(text)
    if scan.has_malformed:
        return None
    if scan.quantities:
        quantity = scan.quantities[0]
        if quantity.currency is None:
            return None
        return quantity.value, quantity.currency

    # RU/EN documents also use suffix ISO codes (e.g. ``1 234,56 USD``).
    # Validate the complete numeric token rather than accepting a shorter suffix.
    match = _SUFFIX_MONEY_RE.search(text)
    if match is None:
        return None
    prefix = text[: match.start()].rstrip()
    if prefix and (prefix[-1].isdigit() or prefix[-1] in ",.'’`"):
        return None
    try:
        value = _normalize_number(match.group("num"))
    except ValueError:
        return None
    return value, match.group("code").upper()


''',
)

# ---------------------------------------------------------------------------
# HIGH-3: template ambiguity and evidence-only answer-key quarantine.
# ---------------------------------------------------------------------------

quarantine_rel = "src/halyk_agent/preflight/quarantine.py"
replace_once(
    quarantine_rel,
    '                if isinstance(cell, dict) and (\n                    cell.get("status") is not None or cell.get("actual") is not None\n                ):\n',
    '                if isinstance(cell, dict) and (\n'
    '                    cell.get("status") is not None\n'
    '                    or cell.get("actual") is not None\n'
    '                    or cell.get("evidence_txn_id") is not None\n'
    '                ):\n',
)

discover_rel = "src/halyk_agent/preflight/discover.py"
replace_once(
    discover_rel,
    '    root_ledgers = [item for item in ledgers if Path(item.path).parent == root]\n'
    '    primary_ledger = sorted(root_ledgers or ledgers, key=lambda r: r.path)[0]\n'
    '    submission_template = sorted(templates, key=lambda r: r.path)[0]\n',
    '    root_ledgers = [item for item in ledgers if Path(item.path).parent == root]\n'
    '    primary_ledger = sorted(root_ledgers or ledgers, key=lambda r: r.path)[0]\n'
    '    root_templates = [item for item in templates if Path(item.path).parent == root]\n'
    '    template_candidates = root_templates or templates\n'
    '    if len(template_candidates) != 1:\n'
    '        paths = sorted(item.path for item in template_candidates)\n'
    '        raise DatasetAdapterError(f"ambiguous submission templates: {paths}")\n'
    '    submission_template = template_candidates[0]\n',
)

solve_rel = "src/halyk_agent/solver/solve.py"
replace_once(
    solve_rel,
    '            if isinstance(cell, dict) and (\n                cell.get("status") is not None or cell.get("actual") is not None\n            ):\n',
    '            if isinstance(cell, dict) and (\n'
    '                cell.get("status") is not None\n'
    '                or cell.get("actual") is not None\n'
    '                or cell.get("evidence_txn_id") is not None\n'
    '            ):\n',
)

# ---------------------------------------------------------------------------
# BLOCKER-2: preserve compile failures without inventing fake definitions.
# Downstream executes the successfully compiled subset; final publication fills
# failed template cells with explicit nulls tied to CovenantCompileFailure.
# ---------------------------------------------------------------------------

transactions_rel = "src/halyk_agent/app/transactions.py"
replace_once(
    transactions_rel,
    '    if routing_scenarios != covenant_scenarios:\n'
    '        raise TransactionServiceError(\n'
    '            "routing scenario universe incompatible with covenant scenario universe",\n'
    '            code="SCENARIO_UNIVERSE_MISMATCH",\n'
    '        )\n'
    '    if not facts_scenarios.issubset(covenant_scenarios):\n'
    '        raise TransactionServiceError(\n'
    '            "accepted facts scenario universe incompatible with covenants",\n'
    '            code="FACTS_SCENARIO_MISMATCH",\n'
    '        )\n',
    '    if not covenant_scenarios.issubset(routing_scenarios):\n'
    '        raise TransactionServiceError(\n'
    '            "covenant scenario universe is not a subset of routing scenarios",\n'
    '            code="SCENARIO_UNIVERSE_MISMATCH",\n'
    '        )\n'
    '    if not facts_scenarios.issubset(routing_scenarios):\n'
    '        raise TransactionServiceError(\n'
    '            "accepted facts scenario universe incompatible with routing",\n'
    '            code="FACTS_SCENARIO_MISMATCH",\n'
    '        )\n',
)

evaluation_rel = "src/halyk_agent/app/evaluation.py"
replace_once(
    evaluation_rel,
    '    plans = plan_definitions(definitions)\n'
    '    context = EvaluationContext(\n'
    '        amount_contract_version=taxonomy_manifest.amount_contract_version,\n'
    '        calculation_inputs=calculation_inputs,\n'
    '        selector_coverage=selector_coverage,\n'
    '        definition_readiness=definition_readiness,\n'
    '    )\n',
    '    plans = plan_definitions(definitions)\n'
    '    plan_scenarios = {plan.scenario_id for plan in plans}\n'
    '    execution_inputs = tuple(\n'
    '        item for item in calculation_inputs if item.scenario_id in plan_scenarios\n'
    '    )\n'
    '    context = EvaluationContext(\n'
    '        amount_contract_version=taxonomy_manifest.amount_contract_version,\n'
    '        calculation_inputs=execution_inputs,\n'
    '        selector_coverage=selector_coverage,\n'
    '        definition_readiness=definition_readiness,\n'
    '    )\n',
)

pipeline_rel = "src/halyk_agent/solver/pipeline.py"
replace_once(
    pipeline_rel,
    'from halyk_agent.domain.covenant_evaluation import EvaluationContext, EvaluationReport\n',
    'from halyk_agent.domain.covenant_evaluation import EvaluationContext, EvaluationReport\n'
    'from halyk_agent.domain.covenants.models import CovenantReport\n',
)
replace_once(
    pipeline_rel,
    '    taxonomy: TaxonomyReport\n    evaluation: EvaluationReport\n',
    '    covenants: CovenantReport\n    taxonomy: TaxonomyReport\n    evaluation: EvaluationReport\n',
)
replace_once(
    pipeline_rel,
    '    context = EvaluationContext(\n'
    '        amount_contract_version=taxonomy.manifest.amount_contract_version,\n'
    '        calculation_inputs=taxonomy.calculation_inputs,\n'
    '        selector_coverage=taxonomy.selector_coverage,\n'
    '        definition_readiness=taxonomy.definition_readiness,\n'
    '    )\n',
    '    evaluation_scenarios = {plan.scenario_id for plan in evaluation.plans}\n'
    '    context = EvaluationContext(\n'
    '        amount_contract_version=taxonomy.manifest.amount_contract_version,\n'
    '        calculation_inputs=tuple(\n'
    '            item\n'
    '            for item in taxonomy.calculation_inputs\n'
    '            if item.scenario_id in evaluation_scenarios\n'
    '        ),\n'
    '        selector_coverage=taxonomy.selector_coverage,\n'
    '        definition_readiness=taxonomy.definition_readiness,\n'
    '    )\n',
)
replace_once(
    pipeline_rel,
    '        evaluation_dir=evaluation_dir,\n        taxonomy=taxonomy,\n',
    '        evaluation_dir=evaluation_dir,\n        covenants=covenants,\n        taxonomy=taxonomy,\n',
)

final_rel = "src/halyk_agent/solver/submission/final.py"
replace_once(
    final_rel,
    'from halyk_agent.domain.transaction_taxonomy.models import AdjustmentEvent, ClassifiedTransaction\n',
    'from halyk_agent.domain.covenants.models import CovenantCompileFailure\n'
    'from halyk_agent.domain.transaction_taxonomy.models import AdjustmentEvent, ClassifiedTransaction\n',
)
replace_once(
    final_rel,
    '    fallback_results: dict[tuple[str, str], CovenantEvaluationResult] | None = None,\n',
    '    fallback_results: dict[tuple[str, str], CovenantEvaluationResult] | None = None,\n'
    '    compile_failures: tuple[CovenantCompileFailure, ...] = (),\n',
)
replace_once(
    final_rel,
    '    if set(result_map) != template_keys or set(plan_map) != template_keys:\n'
    '        raise SubmissionSchemaError(\n'
    '            "evaluation universe must exactly match submission template keys"\n'
    '        )\n',
    '    result_keys = set(result_map)\n'
    '    plan_keys = set(plan_map)\n'
    '    if result_keys != plan_keys:\n'
    '        raise SubmissionSchemaError("evaluation result/plan universes differ")\n'
    '    if not result_keys.issubset(template_keys):\n'
    '        raise SubmissionSchemaError("evaluation contains keys outside submission template")\n'
    '    failure_map: dict[tuple[str, str], CovenantCompileFailure] = {}\n'
    '    for failure in compile_failures:\n'
    '        key = (failure.scenario_id, failure.clause_id)\n'
    '        if key in failure_map:\n'
    '            raise SubmissionSchemaError(f"duplicate compile failure for {key}")\n'
    '        failure_map[key] = failure\n'
    '    if set(failure_map) & result_keys:\n'
    '        raise SubmissionSchemaError("compiled results overlap covenant compile failures")\n'
    '    missing_keys = template_keys - result_keys\n'
    '    if set(failure_map) != missing_keys:\n'
    '        raise SubmissionSchemaError(\n'
    '            "missing evaluation keys must exactly match covenant compile failures"\n'
    '        )\n',
)
replace_once(
    final_rel,
    '            strict_result = result_map[key]\n            plan = plan_map[key]\n',
    '            if key not in result_map:\n'
    '                failure = failure_map[key]\n'
    '                answers[scenario_id][clause_id] = CovenantCell()\n'
    '                unresolved.append(\n'
    '                    {\n'
    '                        "scenario_id": scenario_id,\n'
    '                        "covenant_id": clause_id,\n'
    '                        "evaluation_status": "COMPILE_FAILURE",\n'
    '                        "activation_state": "NOT_APPLICABLE",\n'
    '                        "reason_codes": [failure.status.value],\n'
    '                        "compile_reason": failure.reason,\n'
    '                    }\n'
    '                )\n'
    '                continue\n'
    '            strict_result = result_map[key]\n'
    '            plan = plan_map[key]\n',
)
replace_once(
    solve_rel,
    '            fallback_results=fallback.results,\n        )\n',
    '            fallback_results=fallback.results,\n'
    '            compile_failures=pipeline.covenants.failures,\n'
    '        )\n',
)

# ---------------------------------------------------------------------------
# HIGH-4: harden P5 competition bridge without pretending its assumption is
# strict Stage 5E evidence.  Parse complete money tokens, scan the complete Note
# 7 section and close the gate whenever a competing movement class is named.
# ---------------------------------------------------------------------------

fallback_rel = "src/halyk_agent/solver/fallbacks.py"
replace_once(
    fallback_rel,
    'from halyk_agent.domain.ids import deterministic_id\n',
    'from halyk_agent.domain.fact_extraction.text_locate import parse_money\n'
    'from halyk_agent.domain.ids import deterministic_id\n',
)
replace_between(
    fallback_rel,
    '_MONEY = r"\\$\\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\\.[0-9]+)?)"\n',
    '@dataclass(frozen=True, slots=True)\n',
    '''_OPENING_RE = re.compile(r"Net book value at the beginning of the year\\s*", re.I)
_DEPRECIATION_RE = re.compile(r"Depreciation charge for the year\\s*", re.I)
_CLOSING_RE = re.compile(r"Net book value at the end of the year\\s*", re.I)
_NO_DISPOSALS_RE = re.compile(
    r"There were no disposals of property, plant and equipment during the year", re.I
)
_NO_OTHER_MOVEMENTS_RE = re.compile(r"There were no other movements[^.\\n]*", re.I)
_NOTE_HEADING_RE = re.compile(r"(?im)^\\s*note\\s+(?P<number>\\d+)\\b")
_OTHER_MOVEMENT_RE = re.compile(
    r"\\b(?:"
    r"additions?|acquisitions?|business combinations?|"
    r"transfers?(?:\\s+(?:in|out))?|revaluations?|impairments?|"
    r"foreign exchange(?:\\s+movements?)?|fx movements?|"
    r"(?:currency\\s+)?translation(?:\\s+(?:differences?|movements?))?|"
    r"assets? held for sale|write[- ]?offs?|reclassifications?|"
    r"government grants?|capitali[sz]ed borrowing costs?|depletion|"
    r"right[- ]of[- ]use|other movements?"
    r")\\b",
    re.I,
)


def _extract_note_section(full_text: str, number: int) -> str | None:
    headings = list(_NOTE_HEADING_RE.finditer(full_text))
    for index, heading in enumerate(headings):
        if int(heading.group("number")) != number:
            continue
        end = headings[index + 1].start() if index + 1 < len(headings) else len(full_text)
        return full_text[heading.start() : end]
    return None


def _money_after_label(note: str, match: re.Match[str]) -> tuple[Decimal, str] | None:
    # Keep the window narrow enough that a missing value cannot bind to the next
    # row, while the money parser itself enforces complete-token semantics.
    return parse_money(note[match.end() : match.end() + 120])


@dataclass(frozen=True, slots=True)
''',
)
# Remove obsolete helper left immediately after the dataclass/report section marker.
replace_once(
    fallback_rel,
    '\n\ndef _decimal_money(match: re.Match[str]) -> Decimal:\n    return Decimal(match.group(1).replace(",", ""))\n',
    '',
)
replace_between(
    fallback_rel,
    'def _derive_p5_group_capex(\n',
    'def _convert_eur_inputs(\n',
    '''def _derive_p5_group_capex(
    *,
    parsed_dir: Path,
    routing_dir: Path,
) -> tuple[Decimal | None, dict[str, Any] | None]:
    """Conservative competition-only PPE residual bridge for the P5 group source.

    Unlike strict Stage 5E, this bridge still assumes *unmentioned* movement
    families are zero.  It must therefore close whenever the actual Note 7 names
    another movement family, contains malformed money, or is structurally ambiguous.
    """

    docs = _parsed_documents(parsed_dir)
    group_ids = _p5_group_documents(routing_dir)
    candidates: list[tuple[Decimal, dict[str, Any]]] = []
    for document_id in group_ids:
        item = docs.get(document_id)
        if item is None:
            continue
        source_file, full_text = item
        note = _extract_note_section(full_text, 7)
        if note is None:
            continue
        opening_matches = list(_OPENING_RE.finditer(note))
        dep_matches = list(_DEPRECIATION_RE.finditer(note))
        closing_matches = list(_CLOSING_RE.finditer(note))
        if not (
            len(opening_matches) == len(dep_matches) == len(closing_matches) == 1
            and _NO_DISPOSALS_RE.search(note)
        ):
            continue

        scrubbed = _NO_DISPOSALS_RE.sub("", note)
        scrubbed = _NO_OTHER_MOVEMENTS_RE.sub("", scrubbed)
        if _OTHER_MOVEMENT_RE.search(scrubbed):
            continue

        opening = _money_after_label(note, opening_matches[0])
        depreciation = _money_after_label(note, dep_matches[0])
        closing = _money_after_label(note, closing_matches[0])
        if opening is None or depreciation is None or closing is None:
            continue
        currencies = {opening[1], depreciation[1], closing[1]}
        if currencies != {"USD"}:
            continue
        residual = closing[0] - opening[0] + depreciation[0]
        if residual <= 0:
            continue
        candidates.append(
            (
                residual,
                {
                    "strategy": "PPE_ROLL_FORWARD_RESIDUAL_BRIDGE",
                    "scenario_id": "P5",
                    "source_file": source_file,
                    "document_id": document_id,
                    "opening_nbv": str(opening[0]),
                    "depreciation": str(depreciation[0]),
                    "closing_nbv": str(closing[0]),
                    "derived_group_capex": str(residual),
                    "assumption": (
                        "competition-only residual: movement families not named in the "
                        "complete Note 7 section are treated as zero; strict Stage 5E "
                        "remains unresolved"
                    ),
                },
            )
        )
    unique = {value for value, _diagnostic in candidates}
    if len(unique) != 1 or len(candidates) != 1:
        return None, None
    return candidates[0]


''',
)

# ---------------------------------------------------------------------------
# HIGH-5: a correction that creates an amount from a previously absent ledger
# amount has a counterfactual of "no calculation input", not "unknown" or 0-row.
# ---------------------------------------------------------------------------

evidence_rel = "src/halyk_agent/solver/evidence.py"
replace_once(
    evidence_rel,
    '    if not relevant:\n        return None\n\n    # Restoration is deterministic and conservative.  Category is restored before\n',
    '    if not relevant:\n        return None\n\n'
    '    amount_was_absent = any(\n'
    '        event.event_type is AdjustmentEventType.AMOUNT_CORRECTION\n'
    '        and event.before.get("effective_amount") in {None, ""}\n'
    '        and classified_row.original_amount is None\n'
    '        for event in relevant\n'
    '    )\n'
    '    if amount_was_absent:\n'
    '        return context.model_copy(\n'
    '            update={\n'
    '                "calculation_inputs": tuple(\n'
    '                    item\n'
    '                    for item in context.calculation_inputs\n'
    '                    if item.input_id != current.input_id\n'
    '                )\n'
    '            }\n'
    '        )\n\n'
    '    # Restoration is deterministic and conservative.  Category is restored before\n',
)

# ---------------------------------------------------------------------------
# Regressions directly reproducing the destructive-audit findings.
# ---------------------------------------------------------------------------

_write(
    "tests/facts/test_parse_money_contract.py",
    '''"""Regression contract for complete Stage 5E monetary token parsing."""

from __future__ import annotations

from decimal import Decimal

import pytest

from halyk_agent.domain.fact_extraction.text_locate import parse_money


@pytest.mark.parametrize(
    ("raw", "value", "currency"),
    [
        ("$300,000", Decimal("300000"), "USD"),
        ("$300000", Decimal("300000"), "USD"),
        ("$1250000", Decimal("1250000"), "USD"),
        ("$12345678", Decimal("12345678"), "USD"),
        ("$21847362.55", Decimal("21847362.55"), "USD"),
        ("USD 300000", Decimal("300000"), "USD"),
        ("300 000 USD", Decimal("300000"), "USD"),
        ("1 234,56 EUR", Decimal("1234.56"), "EUR"),
    ],
)
def test_parse_money_consumes_the_complete_value(raw: str, value: Decimal, currency: str) -> None:
    assert parse_money(raw) == (value, currency)


@pytest.mark.parametrize(
    "raw",
    [
        "$3OO,OOO",
        "$1O,000.00",
        "$300,00",
        "$300,,000",
        "$1,,234,567",
        "$300,,,000",
    ],
)
def test_parse_money_never_publishes_a_shorter_valid_prefix(raw: str) -> None:
    assert parse_money(raw) is None
''',
)

_write(
    "tests/covenants/test_opus_redteam_regressions.py",
    '''"""Destructive-audit regressions for threshold token integrity."""

from __future__ import annotations

from decimal import Decimal

import pytest

from halyk_agent.domain.covenants.parse import collect_threshold_candidates, scan_money_quantities
from halyk_agent.domain.covenants.quantity import QuantityType


@pytest.mark.parametrize(
    ("text", "quantity_type", "expected"),
    [
        ("не должен превышать 1,68x", QuantityType.RATIO, Decimal("1.68")),
        ("не более 3,0x", QuantityType.RATIO, Decimal("3.0")),
        ("не более 30,5%", QuantityType.PERCENT, Decimal("30.5")),
    ],
)
def test_comma_decimal_thresholds_are_not_parsed_from_fractional_suffix(
    text: str, quantity_type: QuantityType, expected: Decimal
) -> None:
    candidates = collect_threshold_candidates(text)
    matches = [item for item in candidates if item.quantity.quantity_type is quantity_type]
    assert len(matches) == 1
    assert matches[0].quantity.value == expected


@pytest.mark.parametrize("raw", ["$300,,000", "$1,,234,567", "$300,,,000"])
def test_adjacent_money_separators_fail_closed(raw: str) -> None:
    scan = scan_money_quantities(raw)
    assert scan.has_malformed is True
    assert scan.quantities == ()
''',
)

_write(
    "tests/ingestion/test_template_ambiguity.py",
    '''"""Submission-template ambiguity and answer-key quarantine regressions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from halyk_agent.preflight.discover import DatasetAdapterError, discover_and_sanitize


def _template(*, evidence: str | None = None) -> dict[str, object]:
    return {
        "team": "team",
        "contact_email": "team@example.com",
        "model": "model",
        "answers": {
            "S1": {
                "6.1": {"status": None, "actual": None, "evidence_txn_id": evidence}
            }
        },
    }


def _base(root: Path) -> None:
    (root / "master_ledger_2025.csv").write_text(
        "txn_id,amount,currency\\nTXN-S1-0001,1,USD\\n", encoding="utf-8"
    )
    (root / "CASE.ru.md").write_text(
        "covenant scenario limit", encoding="utf-8"
    )


def test_evidence_only_answer_key_is_quarantined(tmp_path: Path) -> None:
    _base(tmp_path)
    (tmp_path / "submission_template.json").write_text(
        json.dumps(_template()), encoding="utf-8"
    )
    (tmp_path / "aaa_answers.json").write_text(
        json.dumps(_template(evidence="TXN-S1-0001")), encoding="utf-8"
    )
    manifest, _inspection = discover_and_sanitize(tmp_path)
    assert Path(manifest.submission_template.path).name == "submission_template.json"
    assert any(Path(item.path).name == "aaa_answers.json" for item in manifest.quarantined)


def test_multiple_root_templates_fail_closed(tmp_path: Path) -> None:
    _base(tmp_path)
    for name in ("a.json", "b.json"):
        (tmp_path / name).write_text(json.dumps(_template()), encoding="utf-8")
    with pytest.raises(DatasetAdapterError, match="ambiguous submission templates"):
        discover_and_sanitize(tmp_path)


def test_root_template_wins_over_nested_decoy(tmp_path: Path) -> None:
    _base(tmp_path)
    (tmp_path / "submission_template.json").write_text(
        json.dumps(_template()), encoding="utf-8"
    )
    nested = tmp_path / "documents"
    nested.mkdir()
    (nested / "00_old_template.json").write_text(json.dumps(_template()), encoding="utf-8")
    manifest, _inspection = discover_and_sanitize(tmp_path)
    assert Path(manifest.submission_template.path).parent == tmp_path
''',
)

_write(
    "tests/solver/test_partial_publication.py",
    '''"""A covenant compile failure must cost one cell, never the whole submission."""

from __future__ import annotations

from decimal import Decimal

import pytest

from halyk_agent.domain.covenant_evaluation import (
    EvaluationExecutor,
    EvaluationManifest,
    EvaluationReport,
    plan_definition,
)
from halyk_agent.domain.covenants.ast import MetricCategory, Sum, TransactionSet
from halyk_agent.domain.covenants.models import CompileStatus, CovenantCompileFailure
from halyk_agent.domain.covenants.quantity import QuantityType, TypedQuantity
from halyk_agent.solver.errors import SubmissionSchemaError
from halyk_agent.solver.submission.final import build_final_submission
from tests.covenant_evaluation._helpers import _context, _definition, _input, _selector


def _evaluation() -> tuple[EvaluationReport, object]:
    selector = _selector(MetricCategory.REVENUE)
    definition = _definition(
        Sum(of=TransactionSet(selector=selector)),
        selectors=(selector,),
        threshold=TypedQuantity(
            quantity_type=QuantityType.MONEY,
            value=Decimal("100"),
            currency="USD",
        ),
    )
    context = _context(definition, (_input("i1", "125"),))
    plan = plan_definition(definition)
    result = EvaluationExecutor().execute(plan, context)
    manifest = EvaluationManifest(
        covenant_manifest_hash="c",
        taxonomy_manifest_hash="t",
        calculation_inputs_hash="i",
        selector_coverage_hash="s",
        definition_readiness_hash="d",
        plan_count=1,
        result_count=1,
        resolved_count=1,
        unresolved_count=0,
        error_count=0,
        not_activated_count=0,
        compliant_count=1,
        breach_count=0,
        plans_hash="p",
        results_hash="r",
    )
    return EvaluationReport(manifest=manifest, plans=(plan,), results=(result,)), context


def _template() -> dict[str, object]:
    empty = {"status": None, "actual": None, "evidence_txn_id": None}
    return {
        "team": "team",
        "contact_email": "team@example.com",
        "model": "model",
        "answers": {"S1": {"6.1": dict(empty), "6.2": dict(empty)}},
    }


def test_compile_failure_is_published_as_one_explicit_null_cell() -> None:
    evaluation, context = _evaluation()
    failure = CovenantCompileFailure(
        failure_id="failure-1",
        scenario_id="S1",
        clause_id="6.2",
        status=CompileStatus.MALFORMED_THRESHOLD,
        reason="synthetic malformed threshold",
    )
    document, unresolved = build_final_submission(
        _template(),
        evaluation=evaluation,
        context=context,
        adjustments=(),
        classified=(),
        compile_failures=(failure,),
    )
    assert document.answers["S1"]["6.1"].status is not None
    failed = document.answers["S1"]["6.2"]
    assert failed.status is None
    assert failed.actual is None
    assert failed.evidence_txn_id is None
    assert unresolved == (
        {
            "scenario_id": "S1",
            "covenant_id": "6.2",
            "evaluation_status": "COMPILE_FAILURE",
            "activation_state": "NOT_APPLICABLE",
            "reason_codes": ["MALFORMED_THRESHOLD"],
            "compile_reason": "synthetic malformed threshold",
        },
    )


def test_missing_result_without_compile_failure_remains_a_hard_integrity_error() -> None:
    evaluation, context = _evaluation()
    with pytest.raises(SubmissionSchemaError, match="compile failures"):
        build_final_submission(
            _template(),
            evaluation=evaluation,
            context=context,
            adjustments=(),
            classified=(),
        )
''',
)

# Append evidence regression to the existing helper-rich test module.
evidence_tests = _read("tests/solver/test_causal_evidence.py")
evidence_tests += '''


def test_amount_correction_from_absent_amount_removes_input_in_counterfactual() -> None:
    selector = TransactionSelector(category=MetricCategory.REVENUE)
    definition = _definition(selector)
    current_input = _input("off", "120", scenario_id="S1").model_copy(
        update={"applied_fact_ids": ("fact-off-ledger",)}
    )
    coverage = SelectorCoverageEntry(
        definition_id=definition.definition_id,
        scenario_id=definition.scenario_id,
        category=selector.category,
        related_party_only=False,
        group_level=False,
        status=SelectorReadinessStatus.READY,
        reason_code="OK",
        matching_input_count=1,
    )
    readiness = DefinitionReadinessEntry(
        definition_id=definition.definition_id,
        scenario_id=definition.scenario_id,
        status=SelectorReadinessStatus.READY,
        reason_code="OK",
    )
    context = EvaluationContext(
        amount_contract_version=AMOUNT_CONTRACT_VERSION,
        calculation_inputs=(current_input,),
        selector_coverage=(coverage,),
        definition_readiness=(readiness,),
    )
    plan = plan_definition(definition)
    result = EvaluationExecutor().execute(plan, context)
    adjustment = AdjustmentEvent(
        event_id="event-off-ledger",
        event_type=AdjustmentEventType.AMOUNT_CORRECTION,
        scenario_id="S1",
        fact_id="fact-off-ledger",
        transaction_id="TX-off",
        before={"effective_amount": None, "currency": "USD"},
        after={"effective_amount": "120", "currency": "USD"},
        reason_code="AUTHORITATIVE_AMOUNT_CORRECTION",
    )
    classified = ClassifiedTransaction(
        transaction_id="TX-off",
        source_ledger="ledger.csv",
        source_row_index=1,
        source_sha256="b" * 64,
        scenario_id="S1",
        account_id="REV-1",
        original_amount=None,
        original_currency="USD",
        effective_amount=Decimal("120"),
        effective_currency="USD",
        original_date=date(2025, 6, 1),
        original_category=selector.category,
        effective_category=selector.category,
        counterparty_raw="Customer LLC",
        description="Off-ledger revenue correction",
        classification_status=ClassificationStatus.CLASSIFIED,
        classification_method=ClassificationMethod.AUTHORITATIVE_RECLASSIFICATION,
    )
    assert (
        select_causal_evidence(
            plan=plan,
            result=result,
            context=context,
            adjustments=(adjustment,),
            classified=(classified,),
        )
        == "TX-off"
    )
'''
_write("tests/solver/test_causal_evidence.py", evidence_tests)

fallback_tests = _read("tests/solver/test_competitive_fallbacks.py")
fallback_tests = fallback_tests.replace("import json\n", "import json\n\nimport pytest\n", 1)
fallback_tests += '''


@pytest.mark.parametrize(
    "movement",
    [
        "Acquisitions were recorded during the year.",
        "Transfers in were recorded during the year.",
        "Revaluations were recorded during the year.",
        "Impairments were recorded during the year.",
        "FX movements were recorded during the year.",
        "Currency translation differences were recorded during the year.",
        "Business combination activity occurred during the year.",
        "Assets held for sale movement occurred during the year.",
        "Write-offs were recorded during the year.",
        "Reclassifications were recorded during the year.",
        "Government grants affected PPE during the year.",
        "Capitalised borrowing costs affected PPE during the year.",
        "Right-of-use movement affected PPE during the year.",
    ],
)
def test_p5_bridge_closes_on_plural_and_synonym_competing_movements(
    tmp_path: Path, movement: str
) -> None:
    note = f"""Note 7 — Property, Plant and Equipment
There were no disposals of property, plant and equipment during the year.
Net book value at the beginning of the year $5,000,000
Depreciation charge for the year $400,000
Net book value at the end of the year $5,600,000
{movement}
"""
    parsed, routing = _write_p5_bundle(tmp_path, note)
    assert _derive_p5_group_capex(parsed_dir=parsed, routing_dir=routing) == (None, None)


def test_p5_bridge_scans_complete_note_not_fixed_character_window(tmp_path: Path) -> None:
    note = """Note 7 — Property, Plant and Equipment
There were no disposals of property, plant and equipment during the year.
Net book value at the beginning of the year $5,000,000
Depreciation charge for the year $400,000
Net book value at the end of the year $5,600,000
""" + ("narrative " * 400) + "\nRevaluations were recorded.\nNote 8 — Other\n"
    parsed, routing = _write_p5_bundle(tmp_path, note)
    assert _derive_p5_group_capex(parsed_dir=parsed, routing_dir=routing) == (None, None)


def test_p5_bridge_accepts_complete_unseparated_money_without_truncation(tmp_path: Path) -> None:
    note = """Note 7 — Property, Plant and Equipment
There were no disposals of property, plant and equipment during the year.
Net book value at the beginning of the year $5000000
Depreciation charge for the year $400000
Net book value at the end of the year $5600000
"""
    parsed, routing = _write_p5_bundle(tmp_path, note)
    value, diagnostic = _derive_p5_group_capex(parsed_dir=parsed, routing_dir=routing)
    assert value == Decimal("1000000")
    assert diagnostic is not None


def test_p5_bridge_rejects_ocr_corrupt_money(tmp_path: Path) -> None:
    note = """Note 7 — Property, Plant and Equipment
There were no disposals of property, plant and equipment during the year.
Net book value at the beginning of the year $5,OOO,000
Depreciation charge for the year $400,000
Net book value at the end of the year $5,600,000
"""
    parsed, routing = _write_p5_bundle(tmp_path, note)
    assert _derive_p5_group_capex(parsed_dir=parsed, routing_dir=routing) == (None, None)
'''
_write("tests/solver/test_competitive_fallbacks.py", fallback_tests)

print("Opus targeted fixes applied successfully")
