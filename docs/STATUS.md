# Status

Current stage: 4  
Stage status: IMPLEMENTED — Docker-free stabilization in progress

Authoritative profile: **FULL** (competition)

## Completed (implemented; live PostgreSQL verification may still be pending)

- secure dataset inspection
- canonical document parsing
- exact evidence spans
- deterministic structure-aware chunking
- FULL multilingual embeddings (**multilingual-e5-small**, 384-d)
- PostgreSQL FTS plus exact vector retrieval (`postgres_numpy_exact` default; optional pgvector)
- RRF hybrid fusion
- optional cross-encoder reranker (disabled by default; large model approval required)
- offline E5-small embedding from existing cache
- retrieval evaluation harness

## Authoritative competition path

```text
Docling/pypdf parsing
→ structure-aware chunking
→ multilingual-e5-small dense embeddings (384-d)
→ PostgreSQL Full-Text Search (simple, OR lexemes)
→ postgres_numpy_exact (or optional pgvector when already installed)
→ RRF fusion
→ evidence-backed ranked results
```

Reranking is **off by default**. Docker is **never required** and is **never launched** by the application.

## Docker

- never required for the competition runtime
- never launched automatically
- `docker-compose.yml` / `Dockerfile` are reference-only and **unverified** in Stage 4.2 (Docker commands were not executed)

## Large models (frozen)

| Model | Status |
|-------|--------|
| BAAI/bge-m3 | `optional_large_model` / `requires_explicit_user_approval` / `not_preverified` |
| BAAI/bge-reranker-v2-m3 | same — disabled by default |

## FAST retrieval

Status: **experimental fallback — frozen — not the competition default — not actively developed**

## Not implemented

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

## Retrieval notes

- Synthetic / context-enriched retrieval text is **not** primary evidence.
- Hard filters apply **before** ranking (OR within a field, AND across fields).
- Default vector backend is **postgres_numpy_exact** (BYTEA float32 + exact NumPy cosine).
- Optional **pgvector** only when the extension is already installed (never auto-installed).
- PostgreSQL FTS default lexical policy is recall-oriented **OR** across lexemes (`simple` config).
- Offline competition runs: use cached E5-small with `HF_HUB_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1`.

## Next gate

Stage 5 — DeepSeek model gateway and structured evidence extraction  
(only after Stage 4 Docker-free verification)
