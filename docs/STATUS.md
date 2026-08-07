# Status

Current stage: **5A.4.1** (Tesseract probe fix + bounded OCR smoke)  
Stage status: **VERIFIED** (7/7 selected pages OCR-succeeded; remaining blocking = 0)  
Previous: Stage 5A.4 contracts landed on `stage-5a.4/selective-ocr`; Stage 5A / 5A.1–5A.3 **VERIFIED** on main

Authoritative profile: **FULL** (competition)

## Authoritative competition pipeline

```text
Docker-free
→ local PostgreSQL (Stage 4 retrieval)
→ Stage 5A:
    preflight → sanitized-manifest solver → baseline submission
    PostParseQualityGate (visual metadata KNOWN|UNKNOWN)
→ Stage 5A.4 (this stage):
    parse outputs → selective blocking-page plan
    → OCR backend probe (no download)
    → selective OCR (only if offline-ready)
    → OCR quality validation
    → provenance-preserving merge
    → PostParseQualityGate again
```

## Stage 5A.4.1 readiness (measured)

| Component | State |
|-----------|--------|
| Tesseract CLI | **offline_ready** (v5.4.0.20240606) |
| tessdata eng/rus/kaz | **present** (exe-relative discovery; measured tessdata ≈ 23.3 MB) |
| Probe bug fixed | empty `TESSDATA_PREFIX=""` no longer injected; sibling `tessdata/` discovered |
| RapidOCR | still not selected (no onnxruntime / cyrillic; no silent fallback) |
| Bounded public OCR | **7 selected / 7 succeeded / 0 remaining blocking** |
| Downloads performed | **none** |

### Selective OCR command

```powershell
uv run python -m halyk_agent ocr probe --json-output
uv run python -m halyk_agent ocr run --parsed ./work/parsed-full --output ./work/ocr-enriched --overwrite --only-required --backend tesseract_cli --source-root ./agentic-bank-public/documents
```

### Commands

```bash
uv run python -m halyk_agent ocr probe
uv run python -m halyk_agent ocr probe --json-output
# ocr run requires offline-ready backend; exits non-zero otherwise
```

### Invariants

- Selective OCR only (default `--only-required`); no whole-document / all-PDF OCR.
- Explicit backend selection; **no silent fallback** between engines.
- No automatic model/language download.
- OCR text has origin=`OCR` + backend identity; embedded text preserved.
- Synthetic failure strings never become evidence.
- Temporary render files use system temp and must be cleaned up.

## Next gate

**Stage 5B — Scenario and Entity Routing** — selective OCR is verified (7/7). Prepare Stage 5B after merge of `stage-5a.4.1/tesseract-probe-fix`.
