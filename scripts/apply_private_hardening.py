from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one replacement, found {count}: {old[:80]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


# Stage 5E: transaction-targeted facts must agree with Stage 5B scenario ownership.
replace_once(
    "src/halyk_agent/domain/fact_extraction/engine.py",
    "    ledger_rows: tuple[LedgerRow, ...] | None = None,\n    allow_network_models: bool = False,",
    "    ledger_rows: tuple[LedgerRow, ...] | None = None,\n"
    "    ledger_txn_scenarios: Mapping[str, str | None] | None = None,\n"
    "    allow_network_models: bool = False,",
)
replace_once(
    "src/halyk_agent/domain/fact_extraction/engine.py",
    "                        ledger_txn_ids=txn_ids,\n                    )",
    "                        ledger_txn_ids=txn_ids,\n"
    "                        ledger_txn_scenarios=ledger_txn_scenarios,\n"
    "                    )",
)
replace_once(
    "src/halyk_agent/domain/fact_extraction/engine.py",
    "                        ledger_txn_ids=txn_ids,\n                        window=window,\n                    )",
    "                        ledger_txn_ids=txn_ids,\n"
    "                        ledger_txn_scenarios=ledger_txn_scenarios,\n"
    "                        window=window,\n"
    "                    )",
)
replace_once(
    "src/halyk_agent/domain/fact_extraction/engine.py",
    "                                        ledger_txn_ids=txn_ids,\n                                        window=window,\n                                    )",
    "                                        ledger_txn_ids=txn_ids,\n"
    "                                        ledger_txn_scenarios=ledger_txn_scenarios,\n"
    "                                        window=window,\n"
    "                                    )",
)

replace_once(
    "src/halyk_agent/app/facts.py",
    "from halyk_agent.adapters.routing.io import load_ledger_csv\n",
    "from halyk_agent.adapters.routing.io import load_ledger_csv\n"
    "from halyk_agent.adapters.transactions.io import load_transaction_links\n",
)
replace_once(
    "src/halyk_agent/app/facts.py",
    "    parsed_dir: Path,\n    output_dir: Path,\n    ledger_path: Path | None = None,",
    "    parsed_dir: Path,\n"
    "    output_dir: Path,\n"
    "    ledger_path: Path | None = None,\n"
    "    routing_dir: Path | None = None,",
)
replace_once(
    "src/halyk_agent/app/facts.py",
    "    ledger_rows: tuple[LedgerRow, ...] | None = None\n    if ledger_path is not None:\n",
    "    ledger_rows: tuple[LedgerRow, ...] | None = None\n"
    "    ledger_txn_scenarios: dict[str, str | None] | None = None\n"
    "    if ledger_path is not None:\n",
)
replace_once(
    "src/halyk_agent/app/facts.py",
    "        ledger_rows = load_ledger_csv(ledger_path)\n\n    cache_dir = output_dir / \".model_cache\" if allow_network_models else None\n",
    "        ledger_rows = load_ledger_csv(ledger_path)\n\n"
    "    if routing_dir is not None:\n"
    "        routing_dir = routing_dir.resolve()\n"
    "        assert_no_gt_access(routing_dir)\n"
    "        links_path = routing_dir / \"transaction_links.jsonl\"\n"
    "        if not links_path.is_file():\n"
    "            raise FactServiceError(\n"
    "                f\"transaction links missing: {links_path}\", code=\"MISSING_ROUTING_LINKS\"\n"
    "            )\n"
    "        links = load_transaction_links(links_path)\n"
    "        ledger_txn_scenarios = {link.txn_id: link.scenario_id for link in links}\n\n"
    "    cache_dir = output_dir / \".model_cache\" if allow_network_models else None\n",
)
replace_once(
    "src/halyk_agent/app/facts.py",
    "        ledger_rows=ledger_rows,\n        allow_network_models=allow_network_models,",
    "        ledger_rows=ledger_rows,\n"
    "        ledger_txn_scenarios=ledger_txn_scenarios,\n"
    "        allow_network_models=allow_network_models,",
)

# Expose the routing integrity context in the standalone CLI too.
replace_once(
    "src/halyk_agent/app/cli.py",
    "    facts_extract.add_argument(\n        \"--ledger\",\n        type=Path,\n        default=None,\n        help=\"Optional ledger CSV for TXN id semantic checks\",\n    )\n",
    "    facts_extract.add_argument(\n"
    "        \"--ledger\",\n"
    "        type=Path,\n"
    "        default=None,\n"
    "        help=\"Optional ledger CSV for TXN id semantic checks\",\n"
    "    )\n"
    "    facts_extract.add_argument(\n"
    "        \"--routing\",\n"
    "        type=Path,\n"
    "        default=None,\n"
    "        help=\"Stage 5B routing output for TXN-to-scenario validation\",\n"
    "    )\n",
)
replace_once(
    "src/halyk_agent/app/cli.py",
    "                    ledger_path=args.ledger,\n                    overwrite=bool(args.overwrite),",
    "                    ledger_path=args.ledger,\n"
    "                    routing_dir=args.routing,\n"
    "                    overwrite=bool(args.overwrite),",
)

# Bounded model rescue is opt-in and only Stage 5E sees the network.
replace_once(
    "src/halyk_agent/config.py",
    "    llm_primary_provider: str = Field(default=\"deepseek\")\n",
    "    semantic_fallback_enabled: bool = Field(default=False)\n"
    "    llm_primary_provider: str = Field(default=\"deepseek\")\n",
)
replace_once(
    "src/halyk_agent/solver/pipeline.py",
    "            ledger_path=materialized.ledger_path,\n            overwrite=False,\n            allow_network_models=False,",
    "            ledger_path=materialized.ledger_path,\n"
    "            routing_dir=routing_dir,\n"
    "            overwrite=False,\n"
    "            allow_network_models=resolved_settings.semantic_fallback_enabled,",
)

# Stage 5F enums for fail-closed scenario/sign/treatment handling.
replace_once(
    "src/halyk_agent/domain/transaction_taxonomy/models.py",
    "    FX_SETTLEMENT_REFERENCE = \"FX_SETTLEMENT_REFERENCE\"\n",
    "    FX_SETTLEMENT_REFERENCE = \"FX_SETTLEMENT_REFERENCE\"\n"
    "    TRANSACTION_TREATMENT_INCLUDE = \"TRANSACTION_TREATMENT_INCLUDE\"\n"
    "    TRANSACTION_TREATMENT_EXCLUDE = \"TRANSACTION_TREATMENT_EXCLUDE\"\n",
)
replace_once(
    "src/halyk_agent/domain/transaction_taxonomy/models.py",
    "    FACT_LINK_AMBIGUOUS = \"FACT_LINK_AMBIGUOUS\"\n",
    "    FACT_LINK_AMBIGUOUS = \"FACT_LINK_AMBIGUOUS\"\n"
    "    FACT_TXN_SCENARIO_MISMATCH = \"FACT_TXN_SCENARIO_MISMATCH\"\n"
    "    SIGN_DIRECTION_UNRESOLVED = \"SIGN_DIRECTION_UNRESOLVED\"\n",
)
replace_once(
    "src/halyk_agent/domain/transaction_taxonomy/models.py",
    "    FACT_LINK_AMBIGUOUS = \"FACT_LINK_AMBIGUOUS\"\n\n\nclass InputSourceKind",
    "    FACT_LINK_AMBIGUOUS = \"FACT_LINK_AMBIGUOUS\"\n"
    "    FACT_SCENARIO_MISMATCH = \"FACT_SCENARIO_MISMATCH\"\n"
    "    SUBSIDIARY_STATUS_CONFLICT = \"SUBSIDIARY_STATUS_CONFLICT\"\n"
    "    TREATMENT_CONFLICT = \"TREATMENT_CONFLICT\"\n\n\nclass InputSourceKind",
)

# Stage 5F: explicit txn IDs are only valid inside the fact's routed scenario.
replace_once(
    "src/halyk_agent/domain/transaction_taxonomy/engine.py",
    "    TransactionPeriodPayload,\n    TransactionReclassificationPayload,\n)",
    "    TransactionPeriodPayload,\n"
    "    TransactionReclassificationPayload,\n"
    "    TransactionTreatmentPayload,\n"
    "    TreatmentDisposition,\n"
    ")",
)
replace_once(
    "src/halyk_agent/domain/transaction_taxonomy/engine.py",
    "    if payload.transaction_id:\n        if payload.transaction_id in rows_by_txn:\n            return [payload.transaction_id]\n        return []\n    amount = payload.amount.value if payload.amount is not None else None\n    if amount is None:\n        return []\n    hits = [\n        tid\n        for tid in scenario_txns\n        if _abs_amount(_parse_amount(rows_by_txn[tid].amount)) == amount\n    ]\n    return sorted(hits)\n",
    "    if payload.transaction_id:\n"
    "        if payload.transaction_id in scenario_txns and payload.transaction_id in rows_by_txn:\n"
    "            return [payload.transaction_id]\n"
    "        return []\n"
    "    amount = payload.amount.value if payload.amount is not None else None\n"
    "    if amount is None or not payload.counterparty:\n"
    "        return []\n"
    "    counterparty_key = normalize_legal_name_keys(payload.counterparty).identity_key\n"
    "    hits = [\n"
    "        tid\n"
    "        for tid in scenario_txns\n"
    "        if _abs_amount(_parse_amount(rows_by_txn[tid].amount)) == abs(amount)\n"
    "        and normalize_legal_name_keys(rows_by_txn[tid].counterparty).identity_key\n"
    "        == counterparty_key\n"
    "    ]\n"
    "    return sorted(hits)\n",
)

# Amount corrections: cross-scenario targets are invalid and unknown sign stays unresolved.
replace_once(
    "src/halyk_agent/domain/transaction_taxonomy/engine.py",
    "        if raw_tid is None or raw_tid not in classified_mutable:\n            _mark_fact(fact, \"UNUSED\", \"amount correction target missing\")\n            continue\n        amount_by_txn.setdefault(raw_tid, []).append(fact)\n",
    "        if raw_tid is None or raw_tid not in classified_mutable:\n"
    "            _mark_fact(fact, \"UNUSED\", \"amount correction target missing\")\n"
    "            continue\n"
    "        link = links.get(raw_tid)\n"
    "        if link is None or link.scenario_id != fact.scenario_id:\n"
    "            cid = deterministic_id(\"txn-conflict\", ConflictKind.FACT_SCENARIO_MISMATCH.value, fact.fact_id, raw_tid)\n"
    "            conflicts.append(\n"
    "                TaxonomyConflict(\n"
    "                    conflict_id=cid,\n"
    "                    kind=ConflictKind.FACT_SCENARIO_MISMATCH,\n"
    "                    scenario_id=fact.scenario_id,\n"
    "                    transaction_id=raw_tid,\n"
    "                    fact_ids=(fact.fact_id,),\n"
    "                    reason=\"amount correction transaction belongs to another scenario\",\n"
    "                )\n"
    "            )\n"
    "            _mark_fact(fact, \"UNUSED\", \"FACT_TXN_SCENARIO_MISMATCH\")\n"
    "            continue\n"
    "        amount_by_txn.setdefault(raw_tid, []).append(fact)\n",
)
replace_once(
    "src/halyk_agent/domain/transaction_taxonomy/engine.py",
    "        if before is not None and before < 0 and corrected > 0:\n            corrected = -corrected\n        elif before is None:\n            # Missing ledger amount: use authoritative signed magnitude as expense when unclear.\n            corrected = -abs(corrected) if corrected > 0 else corrected\n        state[\"effective_amount\"] = corrected\n",
    "        if before is not None and before < 0 and corrected > 0:\n"
    "            corrected = -corrected\n"
    "        elif before is None and corrected > 0:\n"
    "            state[\"unresolved_reasons\"].append(UnresolvedReason.SIGN_DIRECTION_UNRESOLVED)\n"
    "            for f in facts:\n"
    "                _mark_fact(f, \"UNUSED\", \"positive correction has no source-backed debit/credit direction\")\n"
    "            continue\n"
    "        state[\"effective_amount\"] = corrected\n",
)

# Period facts: validate scenario and compare the full assignment, not only disposition.
replace_once(
    "src/halyk_agent/domain/transaction_taxonomy/engine.py",
    "        if tid not in classified_mutable:\n            _mark_fact(fact, \"UNUSED\", \"period fact target missing\")\n            continue\n        period_by_txn.setdefault(tid, []).append(fact)\n    for tid, facts in sorted(period_by_txn.items()):\n        dispositions = {f.payload.disposition for f in facts}  # type: ignore[union-attr]\n        if len(dispositions) > 1:\n",
    "        if tid not in classified_mutable:\n"
    "            _mark_fact(fact, \"UNUSED\", \"period fact target missing\")\n"
    "            continue\n"
    "        link = links.get(tid)\n"
    "        if link is None or link.scenario_id != fact.scenario_id:\n"
    "            cid = deterministic_id(\"txn-conflict\", ConflictKind.FACT_SCENARIO_MISMATCH.value, fact.fact_id, tid)\n"
    "            conflicts.append(\n"
    "                TaxonomyConflict(\n"
    "                    conflict_id=cid, kind=ConflictKind.FACT_SCENARIO_MISMATCH,\n"
    "                    scenario_id=fact.scenario_id, transaction_id=tid,\n"
    "                    fact_ids=(fact.fact_id,), reason=\"period fact transaction belongs to another scenario\",\n"
    "                )\n"
    "            )\n"
    "            _mark_fact(fact, \"UNUSED\", \"FACT_TXN_SCENARIO_MISMATCH\")\n"
    "            continue\n"
    "        period_by_txn.setdefault(tid, []).append(fact)\n"
    "    for tid, facts in sorted(period_by_txn.items()):\n"
    "        assignments = {\n"
    "            (\n"
    "                f.payload.disposition, f.payload.period_label,\n"
    "                f.payload.service_start, f.payload.service_end,\n"
    "            )\n"
    "            for f in facts\n"
    "            if isinstance(f.payload, TransactionPeriodPayload)\n"
    "        }\n"
    "        if len(assignments) > 1:\n",
)

# Treatments are real Stage 5F inputs, not a dead deferred scaffold.
period_anchor = "    # --- FX: preserve only; never derive rate ---\n"
treatment_block = '''    # --- Explicit transaction treatment ---\n    treatment_by_txn: dict[str, list[FactRecord]] = {}\n    for fact in _facts_by_kind(accepted_facts, FactKind.TRANSACTION_TREATMENT):\n        payload = fact.payload\n        assert isinstance(payload, TransactionTreatmentPayload)\n        tid = payload.transaction_id\n        state = classified_mutable.get(tid)\n        link = links.get(tid)\n        if state is None:\n            _mark_fact(fact, "UNUSED", "treatment target missing")\n            continue\n        if link is None or link.scenario_id != fact.scenario_id:\n            cid = deterministic_id("txn-conflict", ConflictKind.FACT_SCENARIO_MISMATCH.value, fact.fact_id, tid)\n            conflicts.append(\n                TaxonomyConflict(\n                    conflict_id=cid, kind=ConflictKind.FACT_SCENARIO_MISMATCH,\n                    scenario_id=fact.scenario_id, transaction_id=tid,\n                    fact_ids=(fact.fact_id,), reason="treatment transaction belongs to another scenario",\n                )\n            )\n            _mark_fact(fact, "UNUSED", "FACT_TXN_SCENARIO_MISMATCH")\n            continue\n        treatment_by_txn.setdefault(tid, []).append(fact)\n\n    for tid, facts in sorted(treatment_by_txn.items()):\n        dispositions = {\n            f.payload.disposition\n            for f in facts\n            if isinstance(f.payload, TransactionTreatmentPayload)\n        }\n        state = classified_mutable[tid]\n        if len(dispositions) != 1:\n            cid = deterministic_id("txn-conflict", ConflictKind.TREATMENT_CONFLICT.value, tid)\n            conflicts.append(\n                TaxonomyConflict(\n                    conflict_id=cid, kind=ConflictKind.TREATMENT_CONFLICT,\n                    scenario_id=state["scenario_id"], transaction_id=tid,\n                    fact_ids=tuple(f.fact_id for f in facts),\n                    reason="conflicting transaction include/exclude treatments",\n                )\n            )\n            state["conflict_ids"].append(cid)\n            state["unresolved_reasons"].append(UnresolvedReason.FACT_CONFLICT)\n            for fact in facts:\n                _mark_fact(fact, "UNUSED", "conflicting transaction treatments")\n            continue\n        disposition = next(iter(dispositions))\n        flag = "TREATMENT_EXCLUDED" if disposition is TreatmentDisposition.EXCLUDE else "TREATMENT_INCLUDED"\n        if flag not in state["flags"]:\n            state["flags"].append(flag)\n        event_type = (\n            AdjustmentEventType.TRANSACTION_TREATMENT_EXCLUDE\n            if disposition is TreatmentDisposition.EXCLUDE\n            else AdjustmentEventType.TRANSACTION_TREATMENT_INCLUDE\n        )\n        for fact in facts:\n            state["applied_fact_ids"].append(fact.fact_id)\n            state["evidence_refs"].extend(fact.evidence_span_ids)\n            adjustments.append(\n                AdjustmentEvent(\n                    event_id=deterministic_id("adj", event_type.value, fact.fact_id, tid),\n                    event_type=event_type, scenario_id=fact.scenario_id, fact_id=fact.fact_id,\n                    transaction_id=tid, before={}, after={"disposition": disposition.value},\n                    evidence_span_ids=fact.evidence_span_ids,\n                    authority_domain=fact.authority_domain.value, reason_code=event_type.value,\n                )\n            )\n            _mark_fact(fact, "CONSUMED", "transaction treatment applied before Stage 6")\n\n'''
replace_once(
    "src/halyk_agent/domain/transaction_taxonomy/engine.py",
    period_anchor,
    treatment_block + period_anchor,
)

# FX transaction references also obey scenario ownership.
replace_once(
    "src/halyk_agent/domain/transaction_taxonomy/engine.py",
    "        payload = fact.payload\n        assert isinstance(payload, FxRatePayload)\n        adjustments.append(\n",
    "        payload = fact.payload\n"
    "        assert isinstance(payload, FxRatePayload)\n"
    "        if payload.transaction_id is not None:\n"
    "            link = links.get(payload.transaction_id)\n"
    "            if link is None or link.scenario_id != fact.scenario_id:\n"
    "                cid = deterministic_id(\"txn-conflict\", ConflictKind.FACT_SCENARIO_MISMATCH.value, fact.fact_id, payload.transaction_id)\n"
    "                conflicts.append(\n"
    "                    TaxonomyConflict(\n"
    "                        conflict_id=cid, kind=ConflictKind.FACT_SCENARIO_MISMATCH,\n"
    "                        scenario_id=fact.scenario_id, transaction_id=payload.transaction_id,\n"
    "                        fact_ids=(fact.fact_id,), reason=\"FX transaction belongs to another scenario\",\n"
    "                    )\n"
    "                )\n"
    "                _mark_fact(fact, \"UNUSED\", \"FACT_TXN_SCENARIO_MISMATCH\")\n"
    "                continue\n"
    "        adjustments.append(\n",
)

# Standalone subsidiary enrichment must not be first-wins even if upstream artifacts are forged.
old_sub = '''        sub_status = SubsidiaryStatusKind.UNKNOWN\n        for fact in subsidiary_by_scenario.get(scenario_id, ()):\n            payload = fact.payload\n            assert isinstance(payload, SubsidiaryStatusPayload)\n            keys = normalize_legal_name_keys(payload.entity_name)\n            if keys.identity_key != state["counterparty_identity_key"]:\n                continue\n            if payload.status is SubsidiaryKind.UNRESTRICTED:\n                sub_status = SubsidiaryStatusKind.UNRESTRICTED\n                scope = EntityScopeKind.SUBSIDIARY\n            elif payload.status is SubsidiaryKind.RESTRICTED:\n                sub_status = SubsidiaryStatusKind.RESTRICTED\n                scope = EntityScopeKind.SUBSIDIARY\n            elif payload.status is SubsidiaryKind.GROUP_MEMBER:\n                sub_status = SubsidiaryStatusKind.GROUP_MEMBER\n                scope = EntityScopeKind.GROUP\n            if fact.fact_id not in state["applied_fact_ids"]:\n                state["applied_fact_ids"].append(fact.fact_id)\n            break\n'''
new_sub = '''        sub_status = SubsidiaryStatusKind.UNKNOWN\n        matching_sub_facts = []\n        for fact in subsidiary_by_scenario.get(scenario_id, ()):\n            payload = fact.payload\n            assert isinstance(payload, SubsidiaryStatusPayload)\n            keys = normalize_legal_name_keys(payload.entity_name)\n            if keys.identity_key == state["counterparty_identity_key"]:\n                matching_sub_facts.append(fact)\n        sub_values = {\n            fact.payload.status\n            for fact in matching_sub_facts\n            if isinstance(fact.payload, SubsidiaryStatusPayload)\n        }\n        if len(sub_values) > 1:\n            cid = deterministic_id("txn-conflict", ConflictKind.SUBSIDIARY_STATUS_CONFLICT.value, tid)\n            conflicts.append(\n                TaxonomyConflict(\n                    conflict_id=cid, kind=ConflictKind.SUBSIDIARY_STATUS_CONFLICT,\n                    scenario_id=scenario_id, transaction_id=tid,\n                    fact_ids=tuple(f.fact_id for f in matching_sub_facts),\n                    reason="conflicting subsidiary statuses for counterparty",\n                )\n            )\n            state["conflict_ids"].append(cid)\n            state["unresolved_reasons"].append(UnresolvedReason.FACT_CONFLICT)\n            for fact in matching_sub_facts:\n                _mark_fact(fact, "UNUSED", "conflicting subsidiary statuses")\n        elif len(sub_values) == 1:\n            value = next(iter(sub_values))\n            if value is SubsidiaryKind.UNRESTRICTED:\n                sub_status = SubsidiaryStatusKind.UNRESTRICTED\n                scope = EntityScopeKind.SUBSIDIARY\n            elif value is SubsidiaryKind.RESTRICTED:\n                sub_status = SubsidiaryStatusKind.RESTRICTED\n                scope = EntityScopeKind.SUBSIDIARY\n            elif value is SubsidiaryKind.GROUP_MEMBER:\n                sub_status = SubsidiaryStatusKind.GROUP_MEMBER\n                scope = EntityScopeKind.GROUP\n            for fact in matching_sub_facts:\n                if fact.fact_id not in state["applied_fact_ids"]:\n                    state["applied_fact_ids"].append(fact.fact_id)\n'''
replace_once("src/halyk_agent/domain/transaction_taxonomy/engine.py", old_sub, new_sub)

# Excluded treatments never enter the Stage 6 input universe.
replace_once(
    "src/halyk_agent/domain/transaction_taxonomy/engine.py",
    "        if state[\"effective_category\"] is None:\n",
    "        if \"TREATMENT_EXCLUDED\" in state[\"flags\"]:\n"
    "            continue\n"
    "        if state[\"effective_category\"] is None:\n",
)
replace_once(
    "src/halyk_agent/domain/transaction_taxonomy/engine.py",
    "        elif fact.fact_kind is FactKind.TRANSACTION_TREATMENT:\n            _mark_fact(\n                fact,\n                \"DEFERRED_STAGE_6\",\n                \"treatment include/exclude is selector/expression-level for Stage 6\",\n            )\n",
    "        elif fact.fact_kind is FactKind.TRANSACTION_TREATMENT:\n"
    "            _mark_fact(fact, \"UNUSED\", \"transaction treatment had no valid Stage 5F target\")\n",
)

# Add-backs attach to a ledger row only when amount+counterparty+period identify it.
replace_once(
    "src/halyk_agent/domain/transaction_taxonomy/engine.py",
    "        if payload.counterparty:\n            cp_key = normalize_legal_name_keys(payload.counterparty).identity_key\n            target_abs = abs(payload.amount.value)\n            for tid, state in classified_mutable.items():\n                if state[\"scenario_id\"] != fact.scenario_id:\n                    continue\n                if state[\"counterparty_identity_key\"] != cp_key:\n                    continue\n                amt = state[\"effective_amount\"]\n                if amt is None:\n                    continue\n                if abs(amt) == target_abs:\n                    ledger_matches.append(tid)\n",
    "        if payload.counterparty and payload.period_start is not None and payload.period_end is not None:\n"
    "            cp_key = normalize_legal_name_keys(payload.counterparty).identity_key\n"
    "            target_abs = abs(payload.amount.value)\n"
    "            for tid, state in classified_mutable.items():\n"
    "                if state[\"scenario_id\"] != fact.scenario_id:\n"
    "                    continue\n"
    "                if state[\"counterparty_identity_key\"] != cp_key:\n"
    "                    continue\n"
    "                amt = state[\"effective_amount\"]\n"
    "                txn_date = state[\"original_date\"]\n"
    "                if amt is None or txn_date is None:\n"
    "                    continue\n"
    "                if not (payload.period_start <= txn_date <= payload.period_end):\n"
    "                    continue\n"
    "                if abs(amt) == target_abs:\n"
    "                    ledger_matches.append(tid)\n",
)

# Related-party threshold lookup is uniqueness-based, never first-wins.
replace_once(
    "src/halyk_agent/domain/transaction_taxonomy/related_party.py",
    "    if not hits:\n        return None, None\n    payload = hits[0].payload\n    assert isinstance(payload, RelatedPartyThresholdPayload)\n    return payload.threshold_percent, hits[0].fact_id\n",
    "    if not hits:\n"
    "        return None, None\n"
    "    values = {\n"
    "        fact.payload.threshold_percent\n"
    "        for fact in hits\n"
    "        if isinstance(fact.payload, RelatedPartyThresholdPayload)\n"
    "    }\n"
    "    if len(values) != 1:\n"
    "        return None, None\n"
    "    value = next(iter(values))\n"
    "    fact_id = sorted(fact.fact_id for fact in hits)[0]\n"
    "    return value, fact_id\n",
)

print("private hardening transformations applied")
