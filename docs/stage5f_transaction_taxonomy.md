# Stage 5F — Deterministic Transaction Taxonomy & Adjustment Inputs

## Boundary

Stage 5F prepares **calculation-ready inputs** for Stage 6.

It does **not**:

- sum covenant metrics
- evaluate Stage 5D AST expressions
- compute ratios / thresholds
- emit COMPLIANT / BREACH / PASS / FAIL
- fill submission cells
- read ground truth
- call network / LLMs (public path)

## Inputs

- Stage 5B routing (`transaction_links.jsonl`, `routing_manifest.json`)
- Stage 5D covenants (`covenant_definitions.jsonl`, manifest)
- Stage 5E accepted facts (`accepted_facts.jsonl`, manifest)
- Master ledger CSV (SHA must match routing)

## Precedence

```
RAW_LEDGER
  → BASE_CLASSIFICATION (description rules; precision > recall)
  → AUTHORITATIVE_RECLASSIFICATION (ACCEPTED applied; REJECTED preserved)
  → AMOUNT_CORRECTION (once; no double count)
  → PERIOD ASSIGNMENT / EXCLUSION
  → RELATED-PARTY / ENTITY SCOPE
  → CALCULATION INPUT (+ derived off-ledger inputs)
```

## Modifier split (5F vs 6)

| Modifier | Stage |
|----------|-------|
| ACCEPTED reclassification application | **5F** |
| REJECTED reclassification preservation / flag | **5F** |
| Amount / period / off-ledger fact application | **5F** |
| `AUDITOR_RECLASSIFICATION_*` formula meaning | **6** |
| `BOTH_NUMERATOR_AND_DENOMINATOR_RECLASS` | **6** |
| `MATERIALITY_FLOOR` | **6** (not global row drop) |
| Selector include/exclude flags | **6** (flags preserved) |

## Related-party comparator

Stage 5E extracts thresholds from source wording `X% or more` / `и более` → **`ownership_percent >= threshold`**.

Identity match uses Stage 5B `identity_key` only (JSC ≠ LLP).

## CLI

```bash
uv run halyk-agent transactions prepare \
  --routing work/smoke5b2/routing \
  --covenants work/smoke5d/covenants-polarity \
  --facts work/smoke5e3/facts \
  --ledger agentic-bank-public/master_ledger_2025.csv \
  --output work/smoke5f/transactions \
  --overwrite
```

## Outputs

- `transaction_taxonomy.jsonl`
- `transaction_adjustments.jsonl`
- `calculation_inputs.jsonl`
- `derived_inputs.jsonl`
- `transaction_conflicts.jsonl`
- `transaction_unresolved.jsonl`
- `selector_coverage.json`
- `fact_consumption.jsonl`
- `qualifying_related_parties.json`
- `stage5f_manifest.json`
- `stage5f_summary.md`
