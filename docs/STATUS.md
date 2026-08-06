# Status

Current stage: **5A.1** (targeted review fixes for 5A + 5A′)  
Stage status: **IMPLEMENTED** (awaiting Opus 5 re-review of B1/H1/H2)  
Previous: Stage 5A + 5A′ IMPLEMENTED; Stage 4 **VERIFIED** (merged)

**Not VERIFIED** until re-review passes.

Authoritative profile: **FULL** (competition)

## Authoritative competition pipeline

```text
Docker-free
→ local PostgreSQL (Stage 4 retrieval)
→ …
→ Stage 5A.1:
    dataset preflight (quarantine answer keys)
    → sanitized manifest
    → competition solver (allowlisted inputs only)
    → schema-valid baseline submission
→ PostParseQualityGate on all parser backends (pypdf + Docling)
→ parse-cache v2 with page-quality / OCR identity
```

## Stage 5A.1 review fixes

| Finding | Fix |
|---------|-----|
| B1 ground-truth reads in solver | Preflight quarantine + solver consumes only `SanitizedDatasetManifest` |
| H1 legacy SUCCESS cache | Cache envelope v2; legacy/missing page-quality → `CACHE_INCOMPATIBLE` |
| H2 Docling bypass | Shared `PostParseQualityGate` after every backend |

### Truthful isolation invariant

The competition solver process and solver package never open, deserialize, or receive ground-truth / answer-key content.

Raw-dataset preflight may inspect candidate JSON solely for quarantine classification (metadata only; no expected answer values in manifests).

### Commands

```bash
# Preferred two-step boundary
uv run python -m halyk_agent dataset preflight --input ./agentic-bank-public --output ./work/preflight
uv run python -m halyk_agent solve --manifest ./work/preflight/sanitized_manifest.json --output ./work/solve-baseline

# Compatibility composition root (preflight then solve; solver still never walks raw root)
uv run python -m halyk_agent solve --dataset ./agentic-bank-public --output ./work/solve-baseline

# Training scorer only
$env:HALYK_MODE = "training"
uv run python -m halyk_agent train-score --dataset ./agentic-bank-public --submission ./work/solve-baseline/submission.json --output ./work/score

# Bounded OCR diagnostic (detection only; no weight downloads)
uv run python -m halyk_agent ocr-diagnose --documents ./agentic-bank-public/documents --output ./work/ocr-diag
```

### Manifests

| File | Contains |
|------|----------|
| `preflight_manifest.json` | Files inspected by preflight; quarantined candidates (path/hash/size/reason); **no answer values** |
| `run_manifest.json` | Files opened by the competition solver only (allowlisted template/ledger/cases) |

### OCR / cache

- Page quality states unchanged; blocking states prevent trusted `SUCCESS`.
- Offline OCR remains **unavailable**; no OCR installation in 5A.1.
- Parse cache schema: `halyk.parse_cache.v2` with page-quality gate version + OCR policy identity.

### Remaining blockers (before Stage 5B+)

- Opus re-review of B1/H1/H2
- Explicit user approval if OCR model/weights install is required
- Covenant calculation / document authority / DSL (not started)
- DeepSeek / LLM fact extraction (not started)

## Next gate

**Opus 5 read-only re-review of B1, H1 and H2** — do not merge until reviewed.
