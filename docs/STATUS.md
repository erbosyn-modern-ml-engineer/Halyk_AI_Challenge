# Status

Current stage: **5A + 5A′**  
Stage status: **IMPLEMENTED** (awaiting Opus 5 read-only review)  
Previous: Stage 4 **VERIFIED** (merged)

Authoritative profile: **FULL** (competition)

## Authoritative competition pipeline

```text
Docker-free
→ local PostgreSQL (Stage 4 retrieval)
→ PostgreSQL FTS (simple, OR lexemes)
→ postgres_numpy_exact
→ multilingual-e5-small (384-d)
→ RRF
→ no reranker by default
→ Stage 5A: dataset adapter → schema-valid baseline submission (no GT access)
→ Stage 5A′: OCR quality gate (pypdf pre-pass; no silent SUCCESS on OCR_REQUIRED)
```

## Stage 5A + 5A′ (this branch)

| Capability | Status |
|------------|--------|
| Dataset adapter (roles + ignore policies) | implemented |
| Competition / training isolation (`HALYK_MODE`) | implemented |
| Ground-truth leakage guards + opened-file audit | implemented |
| Deterministic baseline `submission.json` | implemented |
| Isolated training scorer (Decimal) | implemented |
| Failure-event vocabulary (bounded) | implemented |
| Image-page / OCR_REQUIRED detection | implemented |
| Offline OCR execution | **unavailable** (no model download; explicit approval required) |

### Commands

```bash
# Competition (default HALYK_MODE=competition) — never reads ground truth
uv run python -m halyk_agent solve --dataset ./agentic-bank-public --output ./work/solve-baseline

# Training scorer only
$env:HALYK_MODE = "training"
uv run python -m halyk_agent train-score --dataset ./agentic-bank-public --submission ./work/solve-baseline/submission.json --output ./work/score

# Bounded OCR diagnostic (detection only; no weight downloads)
uv run python -m halyk_agent ocr-diagnose --documents ./agentic-bank-public/documents --output ./work/ocr-diag
```

### OCR quality states

`TEXT_OK` | `LOW_TEXT` | `IMAGE_DOMINANT` | `HEADING_WITHOUT_BODY` | `OCR_REQUIRED` | `OCR_SUCCEEDED` | `OCR_FAILED` | `UNREADABLE`

pypdf must not report trusted `SUCCESS` when any load-bearing page is `OCR_REQUIRED` without OCR processing (downgraded to `PARTIAL` + quality warning).

### Current OCR backend

Offline OCR is **not available** for automatic use. Docling may be installed, but OCR weights are not pre-verified and are not downloaded by this stage. Missing-component reason is recorded as `OCR_BACKEND_UNAVAILABLE`.

### Remaining blockers (before Stage 5B+)

- Explicit user approval if OCR model/weights install is required
- Covenant calculation / document authority / DSL (not started)
- DeepSeek / LLM fact extraction (not started)

## Completed and verified (Stage 4.3)

- secure dataset inspection
- canonical document parsing with exact EvidenceSpan lineage
- deterministic structure-aware chunking
- FULL embeddings: **intfloat/multilingual-e5-small** @ pinned revision (offline cache)
- PostgreSQL FTS on local PostgreSQL 18.4
- exact vector retrieval via **postgres_numpy_exact**
- shared RRF hybrid fusion
- end-to-end inspect → parse → index → search without Docker

## Optional / not required for competition

| Component | Status |
|-----------|--------|
| pgvector | optional, not required |
| BAAI/bge-m3 | optional large model, disabled |
| BAAI/bge-reranker-v2-m3 | optional large model, disabled |
| Docker Compose | reference-only, never required |
| Offline OCR weights | not installed; approval required |

## Not implemented (Stage 5B+)

- DeepSeek / LLM fact extraction
- document version / authority resolver
- Covenant DSL and transaction classification
- calculated covenant answers
- durable workers / decision workflow
- proof-bundle generation
- manually solved public answers

## Next gate

**Opus 5 read-only review of Stage 5A + 5A′** (do not merge until reviewed).
