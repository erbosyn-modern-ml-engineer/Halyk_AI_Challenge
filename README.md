# Halyk Decision Agent

Evidence-first decision agent for the Halyk Bank Agentic Challenge.

One shared domain core drives two runtime profiles. The **authoritative competition path is FULL**.

- **FULL (default / competition)** — local PostgreSQL when available, pypdf + Docling parsing, **multilingual-e5-small** embeddings (384-d), PostgreSQL FTS + exact vectors (`postgres_numpy_exact`; optional pgvector if already installed), RRF. Reranker disabled by default.
- **FAST** — experimental fallback; frozen; not the competition default (local SQLite/E5 path remains for shared chunking/RRF tests only).

## Requirements

- Python 3.12
- [uv](https://github.com/astral-sh/uv)
- Optional: an already-installed local PostgreSQL for live FULL retrieval (`HALYK_POSTGRES_DSN`)
- Docker is **not** required and is **not** used by the competition runtime

## Quick start

```bash
uv sync --group dev --extra full --extra retrieval-full
uv run pytest
uv run uvicorn halyk_agent.app.main:app --reload
uv run python -m halyk_agent --help
```

Health check (default profile FULL):

```text
GET /health
-> {"status":"ok","stage":4,"profile":"full"}
```

## Stage 2 — archive inspection

```bash
uv run python -m halyk_agent inspect --input archive.zip --output ./work/inspection
```

## Stage 3 — document parsing

```bash
uv run python -m halyk_agent parse --inspection ./work/inspection --output ./work/parsed-full --profile full
```

## Stage 4 — FULL indexing and search (authoritative)

Requires a reachable local PostgreSQL DSN (never start Docker from this project):

```powershell
$env:HALYK_POSTGRES_DSN = "postgresql+asyncpg://USER@HOST:PORT/DB"
$env:TORCHDYNAMO_DISABLE = "1"
$env:TORCH_COMPILE_DISABLE = "1"
$env:HF_HUB_OFFLINE = "1"
$env:TRANSFORMERS_OFFLINE = "1"

# Default embeddings = multilingual-e5-small (already cached after first successful load)
uv run python -m halyk_agent models prewarm --profile full --components embeddings

uv run python -m halyk_agent index --parsed ./work/parsed-full --output ./work/retrieval-full --profile full
uv run python -m halyk_agent search --index ./work/retrieval-full --query "лимит по договору" --top-k 5 --profile full --json-output
```

Do **not** pass `--rerank` for the competition path. Large optional models (BGE-M3 / BGE reranker) require `--approve-large-models` or `HALYK_ALLOW_LARGE_MODEL_DOWNLOAD=1`.

Pinned models: [docs/MODEL_PINS.md](docs/MODEL_PINS.md) / [model-lock.json](model-lock.json).

### Docker (optional reference only)

`docker-compose.yml` and related files are **passive deployment references**. They are **not verified** in Stage 4.2 and must not be launched for competition setup.

## Profiles

Set `HALYK_PROFILE=full` (default) or `HALYK_PROFILE=fast` (frozen experimental fallback only).

Optional vector backend override: `HALYK_VECTOR_BACKEND=postgres_numpy_exact` (default portable) or `pgvector` (only if the extension is already installed).

## Licensing notes

- pypdf: dependency, BSD-style license
- Docling: optional dependency, MIT
- sentence-transformers / E5-small: retrieval extras (see MODEL_PINS)
- BGE-M3 / BGE reranker: optional large models (approval required)
- PyMuPDF: intentionally excluded due AGPL/commercial licensing

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Status](docs/STATUS.md)
- [Model pins](docs/MODEL_PINS.md)
- [Upstream pins](docs/UPSTREAM_PINS.md)
- [Third-party notices](THIRD_PARTY_NOTICES.md)

## License

Apache License 2.0
