# Status

Current stage: 4  
Stage status: VERIFIED

## Completed

- secure dataset inspection
- canonical document parsing
- exact evidence spans
- deterministic structure-aware chunking
- multilingual FAST embeddings
- multilingual FULL embeddings
- SQLite FTS5 plus local vector retrieval
- PostgreSQL FTS plus pgvector retrieval
- RRF hybrid fusion
- optional multilingual reranking
- offline model prewarm
- retrieval evaluation harness

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

without a MSVC `cl` compiler (torch inductor). Model weights are downloaded into the Hugging Face hub cache (`~/.cache/huggingface/hub`) on first FULL run.

## Retrieval notes

- Synthetic / context-enriched retrieval text is **not** primary evidence; cite `evidence_span_ids` / `raw_text` only.
- Hard filters apply **before** ranking (OR within a field, AND across fields).
- Default pgvector search is **exact** cosine distance; HNSW is not enabled and no quality claim is made for approximate indexes.
- Offline competition runs require explicit `models prewarm` then `HF_HUB_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1`.

## Next gate

Stage 5 — DeepSeek model gateway and structured evidence extraction
