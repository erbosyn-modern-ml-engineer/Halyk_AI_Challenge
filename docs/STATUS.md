# Status

Current stage: **5A.3** (final medium fixes and merge gate)  
Stage status: **IMPLEMENTED** (awaiting merge after report verification)  
Previous: Stage 5A.2 IMPLEMENTED (B1/H1/H2 fixed); Stage 4 **VERIFIED** (merged)

**Not VERIFIED.** Do not mark Stage 5A VERIFIED until merge review completes.

Authoritative profile: **FULL** (competition)

## Authoritative competition pipeline

```text
Docker-free
→ local PostgreSQL (Stage 4 retrieval)
→ …
→ Stage 5A.2:
    dataset preflight (quarantine answer keys; lazy package __init__)
    → sanitized manifest
    → competition solver (audited FileOpener; allowlisted inputs only)
    → schema-valid baseline submission
→ one PostParseQualityGate on every public parse path
→ pypdf page-image metadata + Docling picture provenance (KNOWN|UNKNOWN)
→ parse-cache v2 with page-quality gate v2 / OCR identity
```

## Stage 5A.2 fixes

| Finding | Fix |
|---------|-----|
| H2-1 empty `page_image_counts` | pypdf emits `PageVisualSignals` (KNOWN/UNKNOWN); production finalize always receives them |
| H2-2 direct `parse()` bypass | `finalize_canonical_parse` / `to_authoritative_parse_result` on all Protocol `parse()` and app paths |
| H2-3 Docling pictures discarded | `extract_docling_page_visuals` from `pictures[].prov[].page_no`; missing → UNKNOWN |
| B1-a transitive quarantine import | `preflight/__init__.py` has no eager implementation imports |
| B1-b optional `opened_paths` | `FileOpener` Protocol requires audit; fail closed before reads/writes |
| Duplicate blocking sets | Domain `is_blocking_page_quality` / `BLOCKING_PAGE_QUALITY_STATES` |
| Dead `trusted_success_blocked` | Removed; OCR facade re-exports domain helpers only |

### Trust / cache identity

- Gate version: `halyk.page_quality_gate.v2`
- Config hash: `page-quality-visual-v2`
- Stage 5A.1 cache entries without visual identity → incompatible (reparse)

### Isolation invariants

- Solver runtime must not load `preflight.discover` / `quarantine` / `service` or `halyk_agent.training`.
- File opens are audited; unaudited openers are rejected before any input read or submission publish.
- Unknown image visibility is never silently treated as verified zero images.

### Test commands

```bash
# Offline suite (live PostgreSQL tests skip when DSN absent/unreachable)
uv run pytest -q

# Live PostgreSQL suite (requires reachable HALYK_POSTGRES_DSN)
# PowerShell: $env:HALYK_POSTGRES_DSN = "postgresql+asyncpg://..."
uv run pytest -q -m postgres
```

Discrepancy note (5A.1 reports): environments with a working DSN reported all tests passed; clean shells without DSN previously hit fixture **errors** (`pytest.fail`). Fixtures now **skip** when DSN is absent/unreachable — errors are not counted as passes.

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

- Offline OCR remains **unavailable**; no OCR installation in 5A.2.
- Authoritative trust gate is `PostParseQualityGate` only (no competing solver OCR trust path).
- Parse cache schema: `halyk.parse_cache.v2` with page-quality gate **v2** + OCR policy identity.

### Remaining blockers (before Stage 5B+)

- Opus 5 final read-only review of H2-1/H2-2/H2-3 and B1-a/B1-b
- Explicit user approval if OCR model/weights install is required
- Covenant calculation / document authority / DSL (not started)
- DeepSeek / LLM fact extraction (not started)

### Stage 5A.3 medium fixes

- Image counting uses `len(page.images)` (no ImageFile materialisation / decode).
- Trust status: PARTIAL requires ≥1 trusted usable (non-blocking) page; all-blocking documents are FAILED.

## Next gate

**Merge `stage-5a.3/final-medium-fixes` into main after report verification.** Do not begin Stage 5B.
