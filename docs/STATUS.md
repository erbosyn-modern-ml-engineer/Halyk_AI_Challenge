# Status

Current stage: **5A.4** (selective provenance-safe OCR)  
Stage status: **IMPLEMENTED — BLOCKED_ON_OCR_BACKEND**  
Previous: Stage 5A / 5A.1–5A.3 **VERIFIED** (merged to main @ `90a7dcc`); Stage 4 **VERIFIED**

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

## Stage 5A.4 readiness (measured)

| Component | State |
|-----------|--------|
| Tesseract CLI | **missing** (`tesseract_executable`) |
| tessdata eng/rus/kaz | **missing** |
| RapidOCR package | present 3.9.2; local Chinese ONNX ~31.7 MB measured |
| onnxruntime | **missing** |
| RapidOCR cyrillic model | **missing** (would download) |
| Offline-ready backend | **none** |
| Downloads performed | **none** |

### Minimal installation proposal (operator approval required)

Do **not** auto-install from this project.

1. Install **Tesseract OCR** CLI for Windows and ensure `tesseract` is on `PATH`.
2. Install language data files: **eng**, **rus**, **kaz** (tessdata).
3. Optionally set `TESSDATA_PREFIX` to the tessdata directory.
4. Re-run: `uv run python -m halyk_agent ocr probe --json-output`
5. Only when `offline_ready=true` for `TESSERACT_CLI`, run selective OCR:

```powershell
uv run python -m halyk_agent ocr run --parsed ./work/parsed-full --output ./work/ocr-enriched --overwrite --only-required --backend tesseract_cli --source-root ./agentic-bank-public/documents
```

Package sizes are not guessed here; measure from the official Tesseract installer / tessdata files after install.

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

**Stage 5B — Scenario and Entity Routing** only after selective OCR is verified **or** remaining OCR limitations are explicitly accepted. Do not start Stage 5B from this branch without that decision.
