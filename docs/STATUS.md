# Status

Current stage: **5C** (document taxonomy and authority resolution)  
Stage status: **COMPLETE on branch** `stage-5c/document-taxonomy-authority` (not merged)  
Previous: Stage 5B.2 merged to `main` @ `32f9704`

Authoritative profile: **FULL** (competition)

## Stage split

| Stage | Question answered |
|-------|-------------------|
| **5B** | Which scenario/entity does this document *belong to*? (ownership/routing) |
| **5C** | What *type* is it, and is it *authoritative* for a fact domain? |
| **5D** (next) | Covenant semantics / thresholds (not started) |

## Stage 5C guarantees

| Concern | Guarantee |
|---------|-----------|
| Taxonomy | Evidence-backed `DocumentType` from content signals (no embeddings) |
| Lifecycle | Separate `DocumentLifecycleStatus` (FINAL/DRAFT/SUPERSEDED/CURRENT_EXECUTED/…) |
| Authority domains | Separate `AuthorityDomain` (COVENANT_TERMS, FINANCIAL_ADJUSTMENTS, …) |
| False authoritative | Prefer `MISSING_AUTHORITY` / `UNRESOLVED` over wrong draft/policy |
| Obsolete agreements | Explicit superseded markers exclude from COVENANT_TERMS |
| KYC | Dossier ≠ policy/procedure; policy never substitutes |
| Evidence | Every accepted classification/authority decision is quote-backed |
| GT isolation | No ground-truth reads; consumes Stage 5B routing + OCR-enriched parses only |

## Measured public smoke (OCR-enriched + Stage 5B.2 routing)

| Signal | Measured |
|--------|----------|
| Documents | 200 |
| Classified / unknown | 163 / 37 |
| LOAN_AGREEMENT | 24 (12 current + 12 superseded) |
| COVENANT_TERMS authoritative | 12/12 scenarios |
| Obsolete agreements rejected | 12 |
| FINANCIAL_ADJUSTMENTS | 12/12 |
| KYC_RELATIONSHIPS | 12/12 dossiers |
| GROUP_STRUCTURE | 1 (`a5cc1400b640.pdf`) |
| TREASURY_FACTS | 1 (P7) |
| Conflicts | 0 |
| Missing authority | 0 |
| GT reads | 0 |
| Determinism | byte-identical |

## Next gate

Opus 5 read-only review of Stage 5C.  
**Do not begin Stage 5D until review passes.**
