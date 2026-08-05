# Halyk Decision Agent

Evidence-first decision agent for the Halyk Bank Agentic Challenge.

One shared domain core drives two runtime profiles:

- **FAST** — local storage, asyncio jobs, pypdf parsing, local retrieval (later)
- **FULL** — PostgreSQL, Redis lease workers (later), pypdf + Docling parsing, hybrid retrieval (later)

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
-> {"status":"ok","stage":3,"profile":"fast"}
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

## Profiles

Set `HALYK_PROFILE=fast` (default) or `HALYK_PROFILE=full`.

Constructing FULL settings does not import Docling. The FULL parse path lazy-imports Docling and raises `ParserDependencyMissingError` if the `full` extra is missing.

## Licensing notes

- pypdf: dependency, BSD-style license
- Docling: optional dependency, MIT
- PyMuPDF: intentionally excluded due AGPL/commercial licensing

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Status](docs/STATUS.md)
- [Upstream pins](docs/UPSTREAM_PINS.md)
- [Third-party notices](THIRD_PARTY_NOTICES.md)

## License

Apache License 2.0
