# Status

Current stage: 3  
Stage status: VERIFIED

## Completed

- Stage 2 safe archive inspection
- FAST PDF text parsing
- FULL Docling parser adapter
- canonical pages, blocks and tables
- exact evidence spans
- deterministic parse quality gate
- local parse cache
- parse CLI

## Not implemented

- OCR production validation, unless explicitly proven
- chunk embeddings
- retrieval
- DeepSeek
- fact extraction
- version resolver
- transaction calculations
- durable workers
- decision workflow
- submission generation

## Docling / Windows note

FULL Docling smoke on Windows may require:

```text
TORCHDYNAMO_DISABLE=1
TORCH_COMPILE_DISABLE=1
```

without a MSVC `cl` compiler (torch inductor). Model weights are downloaded into the Hugging Face hub cache (`~/.cache/huggingface/hub`, e.g. `models--docling-project--docling-models`) on first FULL run.
## Next gate

Stage 4 — Structure-aware chunking and hybrid retrieval
