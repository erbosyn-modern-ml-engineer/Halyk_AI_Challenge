# Status

Current stage: 4  
Stage status: VERIFIED

Authoritative profile: **FULL** (competition)

## Authoritative competition pipeline

```text
Docker-free
→ local PostgreSQL
→ PostgreSQL FTS (simple, OR lexemes)
→ postgres_numpy_exact
→ multilingual-e5-small (384-d)
→ RRF
→ no reranker by default
```

## Completed and verified (Stage 4.3)

- secure dataset inspection
- canonical document parsing with exact EvidenceSpan lineage
- deterministic structure-aware chunking
- FULL embeddings: **intfloat/multilingual-e5-small** @ pinned revision (offline cache)
- PostgreSQL FTS on local PostgreSQL 18.4
- exact vector retrieval via **postgres_numpy_exact** (BYTEA float32 + NumPy cosine)
- shared RRF hybrid fusion
- live migration, rollback, idempotent rebuild, metadata filters
- end-to-end inspect → parse → index → search without Docker

## Optional / not required for competition

| Component | Status |
|-----------|--------|
| pgvector | optional, not required, used only when already installed |
| BAAI/bge-m3 | optional large model, disabled and not verified |
| BAAI/bge-reranker-v2-m3 | optional large model, disabled and not verified |
| Docker Compose | reference-only, never required, not launched for Stage 4 |

## FAST retrieval

Status: **experimental fallback — frozen — not the competition default — not actively developed**

## Not implemented (Stage 5+)

- DeepSeek
- LLM fact extraction
- document version resolver
- entity resolution
- transaction calculations
- policy engine
- durable workers
- decision workflow
- proof-bundle generation
- Halyk submission generation

## Docling / Windows note

FULL Docling smoke on Windows may require:

```text
TORCHDYNAMO_DISABLE=1
TORCH_COMPILE_DISABLE=1
```

## Next gate

Stage 5 — DeepSeek model gateway and structured evidence extraction
