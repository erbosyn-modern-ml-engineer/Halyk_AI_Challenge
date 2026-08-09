from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, got {count}: {old[:100]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


replace_once(
    "src/halyk_agent/domain/transaction_taxonomy/models.py",
    '    AUTHORITATIVE_RECLASSIFICATION = "AUTHORITATIVE_RECLASSIFICATION"\n',
    '    AUTHORITATIVE_RECLASSIFICATION = "AUTHORITATIVE_RECLASSIFICATION"\n'
    '    SEMANTIC_FALLBACK = "SEMANTIC_FALLBACK"\n',
)
replace_once(
    "src/halyk_agent/domain/transaction_taxonomy/models.py",
    '    related_party_unknown_count: int = Field(ge=0)\n',
    '    related_party_unknown_count: int = Field(ge=0)\n'
    '    semantic_model_calls: int = Field(ge=0, default=0)\n'
    '    semantic_fallback_count: int = Field(ge=0, default=0)\n',
)

replace_once(
    "src/halyk_agent/domain/transaction_taxonomy/engine.py",
    "from collections import Counter\n",
    "from collections import Counter\nfrom collections.abc import Mapping\n",
)
replace_once(
    "src/halyk_agent/domain/transaction_taxonomy/engine.py",
    "    fact_requirement_results: tuple[FactRequirementResult, ...] | None = None,\n) -> TaxonomyReport:\n",
    "    fact_requirement_results: tuple[FactRequirementResult, ...] | None = None,\n"
    "    classification_overrides: Mapping[str, MetricCategory] | None = None,\n"
    "    semantic_model_calls: int = 0,\n"
    ") -> TaxonomyReport:\n",
)
replace_once(
    "src/halyk_agent/domain/transaction_taxonomy/engine.py",
    "        hit = classify_description(row.description)\n        status: ClassificationStatus\n",
    "        hit = classify_description(row.description)\n"
    "        semantic_override = (classification_overrides or {}).get(row.txn_id)\n"
    "        if hit.status == \"UNRESOLVED\" and semantic_override is not None:\n"
    "            from halyk_agent.domain.transaction_taxonomy.classify import ClassificationHit\n\n"
    "            hit = ClassificationHit(\n"
    "                status=\"CLASSIFIED\",\n"
    "                category=semantic_override,\n"
    "                rule=\"DEEPSEEK_SEMANTIC_FALLBACK\",\n"
    "            )\n"
    "        status: ClassificationStatus\n",
)
replace_once(
    "src/halyk_agent/domain/transaction_taxonomy/engine.py",
    "        if hit.status == \"CLASSIFIED\":\n            status = ClassificationStatus.CLASSIFIED\n            method = ClassificationMethod.LEDGER_DESCRIPTION_RULE\n",
    "        if hit.status == \"CLASSIFIED\":\n"
    "            status = ClassificationStatus.CLASSIFIED\n"
    "            method = (\n"
    "                ClassificationMethod.SEMANTIC_FALLBACK\n"
    "                if hit.rule == \"DEEPSEEK_SEMANTIC_FALLBACK\"\n"
    "                else ClassificationMethod.LEDGER_DESCRIPTION_RULE\n"
    "            )\n",
)
replace_once(
    "src/halyk_agent/domain/transaction_taxonomy/engine.py",
    "        related_party_unknown_count=rp_unknown,\n",
    "        related_party_unknown_count=rp_unknown,\n"
    "        semantic_model_calls=semantic_model_calls,\n"
    "        semantic_fallback_count=sum(\n"
    "            1\n"
    "            for c in classified_rows\n"
    "            if c.classification_method is ClassificationMethod.SEMANTIC_FALLBACK\n"
    "        ),\n",
)

replace_once(
    "src/halyk_agent/app/transactions.py",
    "import hashlib\nimport os\n",
    "import hashlib\nimport json\nimport os\n",
)
replace_once(
    "src/halyk_agent/app/transactions.py",
    "from halyk_agent.domain.transaction_taxonomy.engine import run_transaction_taxonomy\n",
    "from halyk_agent.config import Settings, get_settings\n"
    "from halyk_agent.domain.transaction_taxonomy.engine import run_transaction_taxonomy\n"
    "from halyk_agent.domain.transaction_taxonomy.semantic_classifier import classify_unresolved_rows\n",
)
replace_once(
    "src/halyk_agent/app/transactions.py",
    "    overwrite: bool = False,\n) -> TaxonomyReport:\n",
    "    overwrite: bool = False,\n"
    "    settings: Settings | None = None,\n"
    ") -> TaxonomyReport:\n",
)
replace_once(
    "src/halyk_agent/app/transactions.py",
    "    report = run_transaction_taxonomy(\n",
    "    resolved_settings = settings or get_settings()\n"
    "    semantic = classify_unresolved_rows(ledger_rows, links, settings=resolved_settings)\n\n"
    "    report = run_transaction_taxonomy(\n",
)
replace_once(
    "src/halyk_agent/app/transactions.py",
    "        fact_requirement_results=requirement_results,\n    )\n",
    "        fact_requirement_results=requirement_results,\n"
    "        classification_overrides=semantic.overrides,\n"
    "        semantic_model_calls=semantic.model_calls,\n"
    "    )\n",
)
replace_once(
    "src/halyk_agent/app/transactions.py",
    "        write_taxonomy_outputs(report, stage_dir)\n",
    "        write_taxonomy_outputs(report, stage_dir)\n"
    "        semantic_path = stage_dir / \"semantic_classification.jsonl\"\n"
    "        semantic_text = \"\\n\".join(\n"
    "            json.dumps(item, ensure_ascii=False, sort_keys=True)\n"
    "            for item in semantic.diagnostics\n"
    "        )\n"
    "        if semantic_text:\n"
    "            semantic_text += \"\\n\"\n"
    "        semantic_path.write_text(semantic_text, encoding=\"utf-8\", newline=\"\\n\")\n",
)

# Pipeline has only one Stage 5F transaction call.
replace_once(
    "src/halyk_agent/solver/pipeline.py",
    "            output_dir=transactions_dir,\n            overwrite=False,\n        )\n",
    "            output_dir=transactions_dir,\n"
    "            overwrite=False,\n"
    "            settings=resolved_settings,\n"
    "        )\n",
)

# High-precision Russian/Kazakh patterns resolve common accounting descriptions before LLM use.
p = Path("src/halyk_agent/domain/transaction_taxonomy/classify.py")
text = p.read_text(encoding="utf-8")
if "# ruff: noqa: RUF001" not in text:
    text = text.replace(
        '"""Deterministic description→MetricCategory classification (precision > recall)."""\n\n',
        '"""Deterministic description→MetricCategory classification (precision > recall)."""\n\n# ruff: noqa: RUF001\n\n',
        1,
    )
marker = "\n_WEAK_OPEX = (\n"
multilingual = r'''
_MULTILINGUAL_RULES: tuple[tuple[str, MetricCategory, re.Pattern[str]], ...] = (
    ("LABOR_RU_KZ", MetricCategory.LABOR, re.compile(
        r"заработн\w*\s+плат|зарплат|оплат\w*\s+труд|жалақ|еңбекақ", re.IGNORECASE
    )),
    ("RENT_RU_KZ", MetricCategory.RENT, re.compile(
        r"аренд|арендн\w*\s+плат|жалға\s+алу|жалдау", re.IGNORECASE
    )),
    ("TAX_RU_KZ", MetricCategory.TAXES, re.compile(
        r"налог|кпн|ндс|салық|ққс|корпоративн\w*\s+подоход", re.IGNORECASE
    )),
    ("UTILITIES_RU_KZ", MetricCategory.UTILITIES, re.compile(
        r"коммунал|электроэнерг|водоснаб|теплоснаб|электр\s+энерг|су\s+жабдық|телеком", re.IGNORECASE
    )),
    ("REVENUE_RU_KZ", MetricCategory.REVENUE, re.compile(
        r"выручк|доход\w*\s+от\s+реализац|түсім|сатудан\s+түскен", re.IGNORECASE
    )),
    ("CAPEX_RU_KZ", MetricCategory.CAPEX, re.compile(
        r"капитальн\w*\s+затрат|приобретени\w*\s+(?:оборудован|основн)|"
        r"жабдық\w*\s+сатып|негізгі\s+құрал", re.IGNORECASE
    )),
    ("OPEX_RU_KZ", MetricCategory.OPEX, re.compile(
        r"операционн\w*\s+расход|эксплуатационн\w*\s+расход|операциялық\s+шығын", re.IGNORECASE
    )),
    ("INTEREST_RU_KZ", MetricCategory.INTEREST_EXPENSE, re.compile(
        r"процентн\w*\s+расход|процент\w*\s+по\s+(?:кредит|займ)|сыйақы\w*\s+шығын", re.IGNORECASE
    )),
    ("INSURANCE_RU_KZ", MetricCategory.INSURANCE_PREMIUMS, re.compile(
        r"страхов|сақтандыру", re.IGNORECASE
    )),
    ("FINANCING_RU_KZ", MetricCategory.FINANCING_INFLOWS, re.compile(
        r"получен\w*\s+(?:кредит|займ)|кредитн\w*\s+транш|қаржыландыру", re.IGNORECASE
    )),
)
'''
if "_MULTILINGUAL_RULES" not in text:
    if marker not in text:
        raise RuntimeError("classifier insertion marker missing")
    text = text.replace(marker, multilingual + marker, 1)
text = text.replace(
    "    for rule_id, category, pattern in _STRONG_RULES:\n",
    "    for rule_id, category, pattern in (*_STRONG_RULES, *_MULTILINGUAL_RULES):\n",
    1,
)
p.write_text(text, encoding="utf-8", newline="\n")
