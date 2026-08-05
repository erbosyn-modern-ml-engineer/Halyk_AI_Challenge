# Upstream pins (Stage 0–4)

Do not update these pins to newer commits without an explicit architecture decision.

| Repository | Default branch | Pinned commit SHA | License | Planned reuse |
|------------|----------------|-------------------|---------|---------------|
| https://github.com/aegra/aegra | main | `1f0076a69bc7cdf5f61b5487bc17d112ee64eb0c` | Apache-2.0 | adapt lease/worker patterns later |
| https://github.com/onyx-dot-app/onyx | main | `e037f31429ac301e48aa4399d75e0b7f8c91fa6a` | MIT Expat (non-`ee/`); reject `ee/` | connector contract reference |
| https://github.com/getzep/graphiti | main | `aab852df94413fd0d55cbea2b7886173020281d5` | Apache-2.0 | temporal fact model reference (PostgreSQL, not Neo4j) |
| https://github.com/Future-House/paper-qa | main | `d7675d7b7eddeb3535e8c260399c5bbeeb818c50` | Apache-2.0 | evidence-session/limit patterns reference |
| https://github.com/pgvector/pgvector-python | master | `60739dfd6cb9d674f32afa4184d43e6aff9dfbcf` | MIT | dependency + RRF algorithm adapted |
| https://github.com/FlagOpen/FlagEmbedding | master | `7ed43d67ec03fbe5c31c0992dbfa941fb1860549` | MIT | BGE model reference only |
| https://github.com/docling-project/docling | main | `9b454c9e88454d95fd04d538c552a3c07bc3c04d` | MIT | Stage 3 FULL optional dependency |
| https://github.com/langchain-ai/langgraph | main | `b2926a0ff9589c28c7e01fe7cdbb337b86d5a4b4` | MIT | pinned dependency later |
| https://github.com/Open-Source-Legal/OpenContracts | main | `401d38c00cade51cba84e73a1297e22a9d8ba620` | MIT | evidence/citation shape reference |
| https://github.com/py-pdf/pypdf | main | `8b6f6fdd1478b142c688501a0d3a093cca539ba8` | BSD-3-Clause style | Stage 3 FAST dependency |

## Stage 4 notes

- RRF adapted from pgvector-python `examples/hybrid_search/rrf.py` into `src/halyk_agent/adapters/retrieval/rrf.py` (see THIRD_PARTY_NOTICES.md).
- Hugging Face model revisions are locked in `model-lock.json` / `docs/MODEL_PINS.md`.
- Default vector search is exact cosine (`<=>`); HNSW is not enabled by default.
- PaperQA inspected for candidate-limit / async patterns only — agent loop not copied.

## Stage 3 dependency resolution notes

### pypdf

- Source commit inspected (default branch at Stage 3 gate): `8b6f6fdd1478b142c688501a0d3a093cca539ba8`
- Package version installed: `pypdf==6.14.2` (resolved via uv / PyPI at Stage 3)
- Reason: current published release matching the FAST text-extraction API (`PdfReader`, `extract_text`, encryption flags)
- License: BSD-style (see upstream LICENSE / THIRD_PARTY_NOTICES.md)
- Use: dependency only — internals not copied

### Docling

- Source commit inspected: `9b454c9e88454d95fd04d538c552a3c07bc3c04d`
- Package version installed: `docling==2.118.0` (matches version declared in upstream `pyproject.toml` at the pinned commit; resolved via PyPI)
- Reason: published version aligned with the pinned source state for `DocumentConverter` / provenance APIs
- Extra: `[project.optional-dependencies] full`
- Use: dependency only — lazy import; FAST profile must run without it

### reportlab (dev / test-only)

- Package: `reportlab` (BSD)
- Use: generate tiny PDF fixtures in tests — not a runtime dependency

### Intentionally excluded

- PyMuPDF / `fitz` — AGPL (incompatible with intended Apache-2.0 distribution without a commercial license)
- LangChain / LlamaIndex — not used as Stage 4 retrieval stack
