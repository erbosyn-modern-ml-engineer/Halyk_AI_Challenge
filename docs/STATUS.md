# Status

Current stage: **5F** (deterministic transaction taxonomy & adjustment inputs)  
Stage status: **IMPLEMENTED on branch** `stage-5f/transaction-taxonomy-adjustments` (not merged)  
Base: Stage 5E.3 on `main` @ `db41b7de368d4dca67015ebd93a07a15304161a5`

Authoritative profile: **FULL** (competition)

## Stage split

| Stage | Question answered |
|-------|-------------------|
| **5B** | Which scenario/entity does this document/transaction belong to? |
| **5C** | What type is it, and is it authoritative for a fact domain? |
| **5D** | What covenant definition/selectors/modifiers apply? |
| **5E** | What trusted structured facts exist in authoritative sources? |
| **5F** | What calculation-ready transaction/adjustment inputs feed Stage 6? |
| **6** (next) | Covenant actuals / compliance (not started) |

## Stage 5F guarantees

| Concern | Guarantee |
|---------|-----------|
| Taxonomy | Reuses Stage 5D `MetricCategory`; no parallel enum |
| Adjustments | Original vs effective amount/category/period preserved |
| REJECTED reclass | Preserved, not applied |
| Off-ledger | Derived inputs only — never fake ledger rows |
| Related party | `>=` threshold from source “or more”; exact identity_key |
| Aggregation | None — Stage 6 owns sums/ratios/thresholds |
| Network / GT | 0 network calls; 0 ground-truth reads |

## Measured public smoke (Stage 5F)

| Signal | Measured |
|--------|----------|
| Ledger rows | 1473 |
| Scenario-linked | 673 |
| Routing noise | 800 |
| Classified / unresolved / conflicts | 673 / 0 / 0 |
| Calculation inputs | 674 (673 ledger + 1 derived) |
| Selectors supported | 66/66 |
| Facts consumed / deferred | 60 / 1 (FX) |

## Next

Claude Opus 5 independent Stage 5F acceptance review.  
**Do not start Stage 6. Do not push/merge until review.**
