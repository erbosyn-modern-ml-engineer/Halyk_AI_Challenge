# Status

Current stage: **5B.2** (final routing correctness: whitespace matching, group relations, evidence provenance)  
Stage status: **COMPLETE on branch** `stage-5b.2/final-routing-fixes` (not merged)  
Previous: Stage 5B.1 on `stage-5b.1/routing-targeted-fixes` @ `5b8a0f5`

Authoritative profile: **FULL** (competition)

## Stage 5B.2 guarantees

| Concern | Guarantee |
|---------|-----------|
| Layout whitespace | Whitespace-normalized search with exact raw-offset EvidenceSpan quotes |
| Group relations | Strong structural predicates only; bare group/группа/segment insufficient |
| Document evidence | Every document-derived assertion carries `source_sha256` |
| Transaction evidence | `TXN_ID_PREFIX` / `ACCOUNT_ID_FALLBACK` persisted as ledger-row provenance |
| Dataset access | Unchanged Stage 5B.1 audited allowlist / GT isolation |
| Ownership vs relevance | Still scenario ownership only; Stage 5C decides relevance/authority |

## Measured public smoke (OCR-enriched Stage 5A.4)

| Signal | Measured |
|--------|----------|
| Scenarios | 12 / 36 cells |
| Accounts | 12/12 |
| Txn-id linked | 673 |
| Account fallback (public) | 0 |
| Documents exact account | 55 |
| Group/segment (true) | 1 (`a5cc1400b640.pdf` → P5) |
| Exact legal-name | 136 |
| Unresolved | 8 |
| Multi-scenario | 0 |
| Near-name contamination | 0 |
| Identity evidence | 1219 (546 document + 673 transaction) |
| Missing `source_sha256` | 0 |
| GT reads | 0 |
| Determinism | byte-identical on re-route |

## Next gate

If Opus 5 re-review finds no BLOCKER/HIGH: merge Stage 5B into `main`, then begin Stage 5C — Document Taxonomy and Authority Resolution.
