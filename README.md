# Halyk Decision Agent

Evidence-first decision agent for the Halyk Bank Agentic Challenge.

One shared domain core drives two runtime profiles:

- **FAST** — local storage, asyncio jobs, pypdf parsing, SQLite FTS5 + local vectors
- **FULL** — PostgreSQL, Redis lease workers (later), pypdf + Docling parsing, PostgreSQL FTS + pgvector + optional reranker

## Requirements

- Python 3.12
- [uv](https://github.com/astral-sh/uv)

## Quick start

```bash
uv sync
uv run pytest
uv run uvicorn halyk_agent.app.main:app --reload
uv run python -m halyk_agent --help
uv run halyk-agent --help
```

Health check:

```text
GET /health
-> {"status":"ok","stage":4,"profile":"fast"}
```

## Stage 2 — archive inspection

```bash
uv run python -m halyk_agent inspect --input archive.zip --output ./work/inspection
```

## Stage 3 — document parsing

```bash
uv run python -m halyk_agent parse --inspection ./work/inspection --output ./work/parsed --profile fast
uv sync --extra full
uv run python -m halyk_agent parse --inspection ./work/inspection --output ./work/parsed-full --profile full --force-docling
```

## Stage 4 — indexing and search

```bash
uv sync --extra retrieval-fast
uv run python -m halyk_agent models prewarm --profile fast --components embeddings
uv run python -m halyk_agent index --parsed ./work/parsed --output ./work/retrieval --profile fast
uv run python -m halyk_agent search --index ./work/retrieval --query "лимит по договору" --top-k 5 --profile fast --json-output
```

FULL (after Compose Postgres + `uv sync --extra full --extra retrieval-full`):

```bash
uv run python -m halyk_agent models prewarm --profile full --components parser,embeddings,reranker
uv run python -m halyk_agent index --parsed ./work/parsed-full --output ./work/retrieval-full --profile full
uv run python -m halyk_agent search --index ./work/retrieval-full --query "лимит по договору" --top-k 5 --profile full --rerank --json-output
```

Offline after prewarm: set `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`.

Pinned models: [docs/MODEL_PINS.md](docs/MODEL_PINS.md) / [model-lock.json](model-lock.json).

## Profiles

Set `HALYK_PROFILE=fast` (default) or `HALYK_PROFILE=full`.

Constructing FULL settings does not import Docling. The FULL parse path lazy-imports Docling and raises `ParserDependencyMissingError` if the `full` extra is missing. Retrieval extras are separate: `retrieval-fast` / `retrieval-full`.

## Licensing notes

- pypdf: dependency, BSD-style license
- Docling: optional dependency, MIT
- sentence-transformers / E5 / BGE: optional retrieval extras (see MODEL_PINS)
- PyMuPDF: intentionally excluded due AGPL/commercial licensing

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Status](docs/STATUS.md)
- [Model pins](docs/MODEL_PINS.md)
- [Upstream pins](docs/UPSTREAM_PINS.md)
- [Third-party notices](THIRD_PARTY_NOTICES.md)

## License

Apache License 2.0
