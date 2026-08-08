# Status

Current stage: **5F.2** (upstream source recovery & final metric contract)
Stage status: **IMPLEMENTED on branch** `stage-5f.2/upstream-source-and-sign-closure` (not merged)
Source: Stage 5F.1 @ `cf1b832e51a7a51d5018e38023f7caeb1c1b1c61`
Base: Stage 5F.1 on `stage-5f.1/transaction-semantic-corrections`

## Measured public smoke (Stage 5F.2 → `work/smoke5f2/transactions`)

| Signal | Measured |
|--------|----------|
| Ledger / linked / noise | 1473 / 673 / 800 |
| Classified / unresolved / conflicts | 673 / 0 / 0 |
| Calculation inputs / derived | 676 / 3 |
| FINANCING_INFLOWS (genuine) | 2 |
| NON_OPERATING_INCOME (interest income) | 14 |
| ONE_TIME_ADD_BACK facts | 3 (materiality deferred to Stage 6) |
| SUBSIDIARY_STATUS (perimeter) | RESTRICTED + UNRESTRICTED recovered |
| GROUP_CAPEX | ABSENT (`INCOMPLETE_PPE_ROLL_FORWARD`) — no invention |
| Selectors READY / TRUE_ZERO / UNRESOLVED | 63 / 1 / 2 |
| Definitions READY / UNRESOLVED | 34 / 2 |
| Related-party TRUE / FALSE / UNKNOWN | 15 / 657 / 1 |
| Amount contract | `halyk.metric_amount.v1` |
| Facts NEEDS_MODEL | 0 |
| Adjustments preserved | 3 ACCEPTED reclass, 2 REJECTED, 2 amount, 2 period, 1 FX, 1 off-ledger |

Unresolved definitions (honest):

- P5 `GROUP_CAPEX_OPERAND_UNRESOLVED` — PPE roll-forward not closed
- P6 `RELATED_PARTY_IDENTITY_UNKNOWN` — damaged OCR identity not source-faithfully recovered

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
| **6** (next) | Covenant actuals / compliance (**not started**) |

## Stage 5F.2 guarantees

| Concern | Guarantee |
|---------|-----------|
| OCR | Tesseract stdout UTF-8 strict; cache `halyk.ocr_cache.v2` |
| ONE_TIME | Source items extracted; never false TRUE_ZERO; materiality = Stage 6 |
| Subsidiary | SECURITY_PERIMETER_THRESHOLD derivation with row+rule evidence |
| GROUP_CAPEX | Closed PPE bridge only; incomplete → ABSENT, not invented |
| FINANCING | Interest income → NON_OPERATING_INCOME; proceeds only in FINANCING_INFLOWS |
| TAX/OPEX | Income/profit tax excluded; operating taxes keep OPEX membership |
| Sign | `source_amount` + `metric_amount` + versioned contract; no `abs()` |
| RP damaged | Wildcard only damaged tokens; unrelated counterparties stay FALSE |
| Aggregation | None — Stage 6 owns sums/ratios/thresholds |
| Network / GT | 0 network calls; 0 ground-truth reads |

## Next

Claude Opus 5 XHIGH final Stage 5F review.
**Do not start Stage 6. Do not push/merge until review.**
