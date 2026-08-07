# Status

Current stage: **5F.1** (transaction semantic correctness closure)
Stage status: **IMPLEMENTED on branch** `stage-5f.1/transaction-semantic-corrections` (not merged)
Source: Stage 5F @ `c92bacd5455fcf6d7a92f4f38025880a17ebd6c7`
Base: Stage 5E.3 on `main` @ `db41b7de368d4dca67015ebd93a07a15304161a5`

## Measured public smoke (Stage 5F.1 → `work/smoke5f1/transactions`)

| Signal | Measured |
|--------|----------|
| Ledger / linked / noise | 1473 / 673 / 800 |
| Classified / unresolved / conflicts | 673 / 0 / 0 |
| Calculation inputs / derived | 674 / 1 |
| Primary REVENUE | 16 |
| OPEX membership candidates | 503 |
| Selectors READY / TRUE_ZERO / UNRESOLVED | 61 / 2 / 3 |
| Definitions READY / UNRESOLVED | 33 / 3 |
| Related-party TRUE / FALSE / UNKNOWN | 15 / 602 / 56 |
| Adjustments preserved | 3 ACCEPTED reclass, 2 REJECTED, 2 amount, 2 period, 1 FX, 1 off-ledger |

## Pipeline map

| Stage | Question |
|-------|----------|
| **5B** | Which scenario owns each ledger row? |
| **5C** | What type is it, and is it authoritative for a fact domain? |
| **5D** | What covenant definition/selectors/modifiers apply? |
| **5E** | What trusted structured facts exist in authoritative sources? |
| **5F** | What calculation-ready transaction/adjustment inputs feed Stage 6? |
| **5F.1** | Semantic memberships, revenue purity, identity/scope fail-closed |
| **6** (next) | Covenant actuals / compliance (not started) |

## Stage 5F.1 guarantees

| Concern | Guarantee |
|---------|-----------|
| OPEX | Specific operating expenses retain OPEX selector membership |
| REVENUE | Customer/operating revenue only — not refunds/rebates/interest income |
| Identity | Legal-form punctuation safe; no fuzzy match; damaged → UNKNOWN |
| Subsidiary | UNRESTRICTED requires evidence; UNKNOWN ≠ UNRESTRICTED |
| GROUP_CAPEX | No borrower CAPEX substitution; unresolved if no group source |
| Routing | Stage 5B only — no txn-id scenario inference |
| Aggregation | None — Stage 6 owns sums/ratios/thresholds |
| Network / GT | 0 network calls; 0 ground-truth reads |

## Next

Claude Opus 5 XHIGH read-only Stage 5F re-review.
**Do not start Stage 6. Do not push/merge until review.**
