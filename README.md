# Halyk Decision Agent

Evidence-first decision agent for the Halyk Bank Agentic Challenge.

One shared domain core drives two runtime profiles:

- **FAST** — local/SQLite storage, direct asyncio jobs, fast parsing, local retrieval
- **FULL** — PostgreSQL, Redis lease workers, quality parsing, hybrid retrieval, LangGraph checkpoints

## Requirements

- Python 3.12
- [uv](https://github.com/astral-sh/uv)

## Quick start

```bash
uv sync --extra dev
uv run pytest
uv run uvicorn halyk_agent.app.main:app --reload
```

Health check:

```text
GET /health
```

## Profiles

Set `HALYK_PROFILE=fast` (default) or `HALYK_PROFILE=full`.

Stage 1 declares profile configuration only. No database, Redis, parser, or LLM connections are opened.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Status](docs/STATUS.md)
- [Upstream pins](docs/UPSTREAM_PINS.md)
- [Third-party notices](THIRD_PARTY_NOTICES.md)

## License

Apache License 2.0
