# Status

Current stage: **5B** (deterministic scenario and entity routing)  
Stage status: **COMPLETE on branch** `stage-5b/scenario-entity-routing` (not merged)  
Previous: Stage 5A.4.1 Tesseract probe fix **VERIFIED** on main (`107b1e4`)

Authoritative profile: **FULL** (competition)

## Authoritative competition pipeline

```text
Docker-free
→ local PostgreSQL (Stage 4 retrieval)
→ Stage 5A:
    preflight → sanitized-manifest solver → baseline submission
    PostParseQualityGate (visual metadata KNOWN|UNKNOWN)
→ Stage 5A.4 / 5A.4.1:
    selective OCR for blocking pages (Tesseract eng+rus+kaz)
→ Stage 5B (this stage):
    sanitized manifest + template + ledger + OCR-enriched CanonicalDocuments
    → scenario universe (template only)
    → transaction/account graph
    → exact document identity routing + borrower candidates
    → RoutingManifest (+ conflicts / diagnostics)
```

## Stage 5B readiness (measured public smoke)

| Signal | Measured |
|--------|----------|
| Scenarios from template | **12** (36 cells) |
| Primary accounts | **12/12** (one account each) |
| Scenario ledger rows linked | **673** / 1473 total |
| Cross-scenario txn contamination | **0** |
| Documents exact-account-linked | **55** |
| Documents unresolved/noise | **145** |
| Multi-scenario documents | **0** |
| Near-name contamination (Shymkent / Ekibastuz) | **none** (distinct borrower norms) |
| DeepSeek / LLM | **none** |
| Retrieval used for ownership | **no** |
| GT reads in solver audit | **0** |

### Route command

```powershell
uv run python -m halyk_agent route `
  --dataset-manifest ./work/preflight/sanitized_manifest.json `
  --parsed ./work/ocr-enriched `
  --output ./work/routing `
  --overwrite
```

### Invariants

- Scenario IDs are opaque and come only from the submission template.
- Exact complete account tokens only (`ACC-7801` ≠ `ACC-7801-08`).
- Account identifiers outrank normalized legal names.
- Conflicts are first-class records; no fuzzy/embedding ownership.
- Document identity assertions require EvidenceSpan provenance.
- Stage 5B does **not** select document authority/version (Stage 5C).

## Next gate

**Stage 5C — Document Taxonomy and Authority Resolution**
