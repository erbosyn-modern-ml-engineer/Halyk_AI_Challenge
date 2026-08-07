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

## Category model (5F.1)

Each calculation input has:

1. **primary / effective category** — display and authoritative single label
2. **selector memberships** — zero or more additional categories for Stage 6 selectors

Example: `INSURANCE_PREMIUMS` memberships → `(INSURANCE_PREMIUMS, OPEX)`.

OPEX hierarchy members (also match OPEX selectors): LABOR, UTILITIES, INSURANCE_PREMIUMS, RENT, TAXES.

LEASE stays **outside** OPEX (P1 is additive `OPEX + LEASE`).

One transaction → one `CalculationInput` / one amount. Memberships never duplicate amounts.

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
- Damaged ownership identities → non-matches are **UNKNOWN**, never FALSE
- No fuzzy base-name matching; no invented aliases

## Subsidiary / group scope

- `CAPITAL_ASSET_TRANSFERS_TO_UNRESTRICTED_SUBS` requires trusted `UNRESTRICTED` status fact
- Bare "to subsidiary" → CAPEX primary + `subsidiary_status=UNKNOWN`
- UNKNOWN never maps to UNRESTRICTED
- `GROUP_CAPEX` requires group-level source provenance (`GROUP_LEVEL_SOURCE`); borrower CAPEX never substitutes

## Selector readiness

Per selector: `READY` | `TRUE_ZERO` | `UNRESOLVED` (+ reason_code).

Unresolved operands (e.g. missing GROUP_CAPEX source, missing unrestricted status) are **not** converted to zero.

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
  --routing work/smoke5b2/routing \
  --covenants work/smoke5d/covenants-polarity \
  --facts work/smoke5e3/facts \
  --ledger agentic-bank-public/master_ledger_2025.csv \
  --output work/smoke5f1/transactions \
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
