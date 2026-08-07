# Status

Current stage: **5B.1** (routing completeness, identity safety, GT-isolation fixes)  
Stage status: **COMPLETE on branch** `stage-5b.1/routing-targeted-fixes` (not merged)  
Previous: Stage 5B on `stage-5b/scenario-entity-routing` @ `d3b80cd`

Authoritative profile: **FULL** (competition)

## Stage 5B.1 guarantees

| Concern | Guarantee |
|---------|-----------|
| Dataset access | Audited `FileOpener` + allowlist∩quarantine empty; GT-like paths rejected |
| Transactions | Strong `TXN_ID_PREFIX` anchors; `ACCOUNT_ID_FALLBACK` for malformed IDs on known accounts |
| Legal identity | `identity_key` preserves legal form; `base_key` never accepts a link |
| Document routing | LEVEL1 account → LEVEL2 declaration → LEVEL3 group/segment → LEVEL4 exact name → UNRESOLVED |
| Ownership vs relevance | Routing assigns scenario concern only; Stage 5C decides relevance/authority |
| Evidence | `identity_evidence.jsonl` persists auditable assertions |

## Measured public smoke (OCR-enriched Stage 5A.4)

| Signal | Measured |
|--------|----------|
| Scenarios | 12 / 36 cells |
| Accounts | 12/12 |
| Txn-id linked | 673 |
| Account fallback (public) | 0 |
| Documents exact account | 55 |
| Group/segment | 6 |
| Exact legal-name | 130 |
| Unresolved | 9 |
| Multi-scenario | 0 |
| Near-name contamination | 0 |
| GT reads | 0 |

## Next gate

Focused Opus 5 read-only re-review of the five Stage 5B High findings.  
**Do not begin Stage 5C until that review passes.**
