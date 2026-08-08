# Status

Current stage: **5F.3** (final input integrity & add-back semantics closure)
Stage status: **IMPLEMENTED on branch** `stage-5f.3/final-input-integrity-closure` (not merged)
Source: Stage 5F.2 @ `5f675d3b0327f8d14b15d1bf0deaec9995d5ae00`
Base: Stage 5F.2 on `stage-5f.2/upstream-source-and-sign-closure`

## Measured public smoke (Stage 5F.3 → `work/smoke5f2/transactions`)

| Signal | Measured |
|--------|----------|
| Ledger / linked / noise | 1473 / 673 / 800 |
| Classified / unresolved / conflicts | 673 / 0 / 0 |
| Calculation inputs / derived | 676 / 3 |
| ONE_TIME_ADD_BACKS memberships | 3 (1 ledger-attached keeps OPEX; 2 fact-derived) |
| GROUP_CAPEX facts | 0 (`INCOMPLETE_PPE_ROLL_FORWARD`) |
| Selectors READY / TRUE_ZERO / UNRESOLVED | 63 / 1 / 2 |
| Definitions READY / UNRESOLVED | 34 / 2 |
| Related-party TRUE / FALSE / UNKNOWN | 15 / 657 / 1 |
| Amount contract | `halyk.metric_amount.v1` |
| Facts NEEDS_MODEL | 0 |
| Fact artifact hash verification | fail-closed (`FACT_ARTIFACT_HASH_MISMATCH`) |

Unresolved definitions (honest):

- P5 `GROUP_CAPEX_OPERAND_UNRESOLVED` — PPE roll-forward not closed
- P6 `RELATED_PARTY_IDENTITY_UNKNOWN` — no trustworthy canonical identity is currently recoverable from accepted source artifacts; P6 remains UNKNOWN

## Pipeline map

| Stage | Question |
|-------|----------|
| **5B** | Which scenario owns each ledger row? |
| **5C** | What type is it, and is it authoritative for a fact domain? |
| **5D** | What covenant definition/selectors/modifiers apply? |
| **5E** | What trusted structured facts exist in authoritative sources? |
| **5F** | What calculation-ready transaction/adjustment inputs feed Stage 6? |
| **5F.1** | Semantic memberships, revenue purity, identity/scope fail-closed |
| **5F.2** | OCR UTF-8 recovery, source facts, financing/tax purity, metric sign contract |
| **5F.3** | Add-back membership integrity, PPE closure soundness, artifact hashes, TRUE_ZERO deps |
| **6** (next) | Covenant actuals / compliance (**not started**) |

## Stage 5F.3 guarantees

| Concern | Guarantee |
|---------|-----------|
| ONE_TIME | Metric membership augmentation — never replaces expense category/OPEX |
| PPE bridge | Every non-addition movement must be proven; `"no disposals"` alone is incomplete |
| Fact artifacts | Manifest hashes verified for accepted facts / results / evidence before publish |
| TRUE_ZERO | Data-driven source dependency gating (not category enum list) |
| P6 docs | No unreproducible OCR-variant claim; UNKNOWN retained honestly |
| Aggregation | None — Stage 6 owns sums/ratios/thresholds |
| Network / GT | 0 network calls; 0 ground-truth reads |

## Next

Claude Opus 5 XHIGH FINAL Stage 5F sign-off.
**Do not start Stage 6. Do not push/merge until review.**
