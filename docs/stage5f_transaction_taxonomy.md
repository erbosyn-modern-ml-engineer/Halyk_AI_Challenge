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

- Stage 5B routing (`transaction_links.jsonl`, `routing_manifest.json`) — **sole** scenario ownership
- Stage 5D covenants (`covenant_definitions.jsonl`, manifest)
- Stage 5E accepted facts (`accepted_facts.jsonl`, manifest)
- Master ledger CSV (SHA must match routing)

Compatibility fail-closed checks:

- ledger SHA == routing ledger identity
- routing scenario universe == covenant scenario universe
- accepted-facts scenarios ⊆ covenant scenarios
- facts `authority_manifest_hash` == covenants `authority_manifest_hash`

## Category model (5F.2)

Each calculation input has:

1. **primary / effective category** — display and authoritative single label
2. **selector memberships** — zero or more additional categories for Stage 6 selectors
3. **amount contract** — `source_amount` (ledger/fact signed) + `metric_amount` (Stage 6 aggregate)

Example: `INSURANCE_PREMIUMS` memberships → `(INSURANCE_PREMIUMS, OPEX)`.

OPEX hierarchy members (also match OPEX selectors): LABOR, UTILITIES, INSURANCE_PREMIUMS, RENT, and **operating** TAXES only.

Corporate income / profit tax stays `TAXES` primary with **no** OPEX membership (`INCOME_TAX_EXCLUDED_FROM_OPEX`).

Interest income is `NON_OPERATING_INCOME` (not `FINANCING_INFLOWS`, not `REVENUE`).

Asset transfers use `CAPITAL_ASSET_TRANSFER`; they enter
`CAPITAL_ASSET_TRANSFERS_TO_UNRESTRICTED_SUBS` only with proven `UNRESTRICTED` status.

LEASE stays **outside** OPEX (P1 is additive `OPEX + LEASE`).

One transaction → one `CalculationInput` / one amount. Memberships never duplicate amounts.

## One-time add-backs (metric membership)

`ONE_TIME_ADD_BACK` facts are **metric-role augmentations**, not reclassifications.

- Ledger-attached: keep original expense category + memberships; add `ONE_TIME_ADD_BACKS`
- Fact-derived (no unique ledger twin): emit a separate positive-magnitude ONE_TIME input
- Exactly one `CalculationInput` per ledger transaction
- Participating in both OPEX and ONE_TIME_ADD_BACKS is intentional for Stage 5D add-back formulas

## Metric amount contract (`halyk.metric_amount.v1`)

Stage 6 must aggregate `metric_amount`, never raw ledger signs.

| Case | source_amount | metric_amount | sign_rule |
|------|---------------|---------------|-----------|
| Normal expense | `-100` | `+100` | `EXPENSE_NEGATE_SOURCE` |
| Expense credit / refund | `+20` | `-20` | `EXPENSE_NEGATE_SOURCE` |
| Revenue / financing proceeds | `+100` | `+100` | `INFLOW_AS_IS` |
| Fact-derived positive magnitude | `+100` | `+100` | `POSITIVE_MAGNITUDE_AS_IS` |

`abs(amount)` is forbidden — credits must reduce expense metrics naturally.
Sign follows the **effective** category after accepted reclassification.

## Precedence

```
RAW_LEDGER
  → BASE_CLASSIFICATION (description rules; precision > recall)
  → AUTHORITATIVE_RECLASSIFICATION (ACCEPTED applied; REJECTED preserved)
  → AMOUNT_CORRECTION (once; no double count)
  → PERIOD ASSIGNMENT / EXCLUSION
  → RELATED-PARTY / SUBSIDIARY STATUS / MEMBERSHIPS
  → CALCULATION INPUT (+ derived off-ledger inputs)
```

## Revenue semantics

REVENUE = genuine customer/operating revenue primitives only.

Refunds, rebates, deposit returns, tax credits, interest income are **not** REVENUE.
Expense credits retain membership in their originating expense family (+ OPEX hierarchy where applicable).

## Related-party / identity

- Exact `identity_key` match only
- Legal-form punctuation canonicalized: `LLP` / `LLP.` / `L.L.P.` (form class preserved; JSC ≠ LLP ≠ TOO)
- Damaged ownership identities: only explicitly corrupted tokens (`?`) may be wildcarded;
  legal form + undamaged tokens must match exactly → `POSSIBLE_MATCH` / UNKNOWN
- Unrelated counterparties remain **FALSE** (no whole-scenario UNKNOWN)
- No edit-distance / fuzzy base-name matching; no invented aliases

## Subsidiary / group scope

- `CAPITAL_ASSET_TRANSFERS_TO_UNRESTRICTED_SUBS` requires trusted `UNRESTRICTED` status fact
- Bare "to subsidiary" → CAPEX primary + `subsidiary_status=UNKNOWN`
- UNKNOWN never maps to UNRESTRICTED
- `GROUP_CAPEX` requires group-level source provenance (`GROUP_LEVEL_SOURCE`); borrower CAPEX never substitutes

## Selector readiness

Per selector: `READY` | `TRUE_ZERO` | `UNRESOLVED` (+ reason_code).

Unresolved operands (e.g. missing GROUP_CAPEX source, missing unrestricted status) are **not** converted to zero.

Source-dependent selectors (`ONE_TIME_ADD_BACKS`, `GROUP_CAPEX`, unrestricted transfers) must not assert
`TRUE_ZERO` from PARTIAL / OCR-corrupted / incomplete source → `UNRESOLVED_SOURCE_QUALITY` (or equivalent).

Trusted empty universes (e.g. P10 RENT after full audit) may remain `TRUE_ZERO`.

Per definition readiness is published for Stage 6 consumption.

## Modifier split (5F vs 6)

| Modifier | Stage |
|----------|-------|
| ACCEPTED reclassification application | **5F** |
| REJECTED reclassification preservation / flag | **5F** |
| Amount / period / off-ledger fact application | **5F** |
| Semantic selector memberships | **5F** |
| `AUDITOR_RECLASSIFICATION_*` formula meaning | **6** |
| `BOTH_NUMERATOR_AND_DENOMINATOR_RECLASS` | **6** |
| `MATERIALITY_FLOOR` | **6** (not global row drop) |
| Selector include/exclude flags | **6** (flags preserved) |

## Related-party comparator

Stage 5E extracts thresholds from source wording `X% or more` / `и более` → **`ownership_percent >= threshold`**.

## CLI

```bash
uv run halyk-agent transactions prepare \
  --routing work/smoke5f2/routing \
  --covenants work/smoke5f2/covenants \
  --facts work/smoke5f2/facts \
  --ledger agentic-bank-public/master_ledger_2025.csv \
  --output work/smoke5f2/transactions \
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
- `definition_readiness.json`
- `fact_consumption.jsonl`
- `qualifying_related_parties.json`
- `stage5f_manifest.json`
- `stage5f_summary.md`
