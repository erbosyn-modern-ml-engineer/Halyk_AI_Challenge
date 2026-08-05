# Third-party notices

This file records upstream sources reviewed for the Halyk Decision Agent.

**Stage 1 status:** Stage 1 has **not** copied upstream implementation code. Sources below were audited in Stage 0 and are pinned for later reuse decisions. When code is adapted in a later stage, each adapted file will gain an entry describing modifications and retained copyright notices.

## Audited sources (Stage 0)

### Aegra

- Repository: https://github.com/aegra/aegra
- Upstream commit: `1f0076a69bc7cdf5f61b5487bc17d112ee64eb0c`
- License: Apache-2.0
- Source files inspected:
  - `libs/aegra-api/src/aegra_api/services/worker_executor.py`
  - `libs/aegra-api/src/aegra_api/services/lease_reaper.py`
  - `libs/aegra-api/src/aegra_api/services/base_executor.py`
  - `libs/aegra-api/src/aegra_api/services/run_executor.py`
  - `libs/aegra-api/src/aegra_api/models/run_job.py`
  - related unit tests under `libs/aegra-api/tests/unit/test_services/`
- Modifications: none in Stage 1 (reference/adapt later for lease patterns only)

### Onyx (Community Edition only)

- Repository: https://github.com/onyx-dot-app/onyx
- Upstream commit: `e037f31429ac301e48aa4399d75e0b7f8c91fa6a`
- License: MIT Expat for content outside `ee/`; Enterprise license under `ee/` (**rejected**)
- Source files inspected:
  - `backend/onyx/connectors/interfaces.py`
  - `backend/onyx/connectors/models.py`
  - `backend/onyx/connectors/README.md`
  - `backend/tests/daily/connectors/` (layout / contract testing pattern)
- Modifications: none in Stage 1

### Graphiti

- Repository: https://github.com/getzep/graphiti
- Upstream commit: `aab852df94413fd0d55cbea2b7886173020281d5`
- License: Apache-2.0
- Source files inspected:
  - `graphiti_core/edges.py`
  - `graphiti_core/graphiti.py`
  - `graphiti_core/utils/bulk_utils.py`
  - `graphiti_core/search/`
  - `graphiti_core/models/edges/`
  - `tests/utils/maintenance/test_edge_operations.py`
- Modifications: none in Stage 1 (Neo4j runtime rejected; temporal fields are conceptual reference)

### PaperQA2

- Repository: https://github.com/Future-House/paper-qa
- Upstream commit: `d7675d7b7eddeb3535e8c260399c5bbeeb818c50`
- License: Apache-2.0
- Source files inspected:
  - `src/paperqa/agents/tools.py`
  - `src/paperqa/agents/main.py`
  - `src/paperqa/agents/env.py`
  - `src/paperqa/docs.py`
  - `src/paperqa/settings.py`
  - `src/paperqa/types.py`
  - `tests/test_agents.py`
- Modifications: none in Stage 1

### pgvector-python

- Repository: https://github.com/pgvector/pgvector-python
- Upstream commit: `60739dfd6cb9d674f32afa4184d43e6aff9dfbcf`
- License: MIT
- Source files inspected:
  - `examples/hybrid_search/rrf.py`
  - `examples/hybrid_search/cross_encoder.py`
- Stage 4 adapted file: `src/halyk_agent/adapters/retrieval/rrf.py`
- Modifications:
  - retained RRF formula `score = Σ 1/(k + rank)` with configurable `k` (default 60);
  - pure in-memory fusion over ranked chunk-id lists (no SQL / connection / table setup copied);
  - returns per-list 1-based ranks with the fused score;
  - deterministic tie-break by `chunk_id` ascending;
  - cross-encoder example used as behavioral reference only — local `CrossEncoderReranker` uses sentence-transformers without copying upstream connection or model wiring.

### FlagEmbedding (reference only)

- Repository: https://github.com/FlagOpen/FlagEmbedding
- Upstream commit inspected (default branch `master` at Stage 4): `7ed43d67ec03fbe5c31c0992dbfa941fb1860549`
- License: MIT
- Use: architectural / model-card reference for BGE-M3 and BGE-reranker-v2-m3; package not vendored

### Docling

- Repository: https://github.com/docling-project/docling
- Upstream commit: `9b454c9e88454d95fd04d538c552a3c07bc3c04d`
- License: MIT
- Source files inspected:
  - `pyproject.toml` (declares version `2.118.0` at pin)
  - `docling/document_converter.py`
  - `docling/datamodel/document.py`
  - `docs/examples/batch_convert.py`
  - `docs/examples/export_tables.py`
  - DoclingDocument / provenance / BoundingBox (`CoordOrigin`) definitions via `docling-core`
- Package installed: `docling==2.118.0` as optional extra `full`
- Modifications: none — dependency adapter only (`DoclingDocumentParser` + mapping)

### pypdf

- Repository: https://github.com/py-pdf/pypdf
- Upstream commit inspected: `8b6f6fdd1478b142c688501a0d3a093cca539ba8`
- License: BSD-style (see upstream LICENSE)
- Package installed: `pypdf>=6.0.0` (resolved `6.14.2`)
- Use: FAST PDF text extraction dependency — internals not copied

### reportlab

- Package: `reportlab` (BSD) — **dev/test only**
- Use: generate tiny synthetic PDFs for Stage 3 tests

### Intentionally excluded: PyMuPDF / fitz

- AGPL / commercial licensing is incompatible with the project's Apache-2.0 distribution model unless a separate commercial license is acquired.
- Regression tests fail if `pymupdf` / `fitz` appear in `pyproject.toml` or under `src/`.

### LangGraph

- Repository: https://github.com/langchain-ai/langgraph
- Upstream commit: `b2926a0ff9589c28c7e01fe7cdbb337b86d5a4b4`
- License: MIT
- Source files inspected:
  - `libs/langgraph/langgraph/graph/state.py`
  - `libs/checkpoint-postgres/README.md`
  - related packaging / security advisory notes
- Modifications: none in Stage 1 (planned as a pinned dependency)

### OpenContracts

- Repository: https://github.com/Open-Source-Legal/OpenContracts
- Upstream commit: `401d38c00cade51cba84e73a1297e22a9d8ba620`
- License: MIT
- Source files inspected:
  - `opencontractserver/documents/models.py`
  - `opencontractserver/annotations/models.py`
  - `opencontractserver/extracts/models.py`
  - `opencontractserver/utils/extraction_grounding.py`
  - `opencontractserver/constants/annotations.py`
- Modifications: none in Stage 1 (Django application rejected; citation/span shapes are conceptual reference)
