# Architecture

## Shared domain core

`halyk_agent.domain` holds the competition-invariant models shared by FAST and FULL:

- evidence spans and facts
- transactions and calculated values (Decimal only)
- rule references and decisions
- applicable version sets and proof bundles
- dataset manifests and schema profiles (Stage 2)

Domain code must not import contracts, adapters, profiles, FastAPI, SQLAlchemy, Redis, LangGraph, Docling, or provider SDKs.

## Profiles

| Concern | FAST | FULL |
|---------|------|------|
| Storage | local / SQLite or memory | PostgreSQL |
| Jobs | direct asyncio | Redis lease + heartbeat + recovery |
| Parsing | fast (PyMuPDF planned) | quality / Docling fallback planned |
| Retrieval | local lexical/vector | PostgreSQL FTS + pgvector |
| Evidence depth | standard | deep |
| Workflow checkpoints | in-process (later) | LangGraph Postgres checkpointer (later) |
| Archive inspection | shared Stage 2 pipeline | shared Stage 2 pipeline |

## Dependency direction

```text
app ──► profiles / contracts / domain / adapters
adapters ──► contracts / domain
contracts ──► domain
domain ──► (stdlib + pydantic only)
```

## Stage 2 data flow

```text
ZIP input
  → ArchiveZipConnector (path safety, zip-bomb limits, streamed extract)
  → SHA-256 hashing + format identification
  → CSV/JSON/JSONL/XLSX sample profilers
  → deterministic role classification
  → DatasetManifest + SchemaProfileDocument
  → atomic write: manifest.json / schema_profile.json / inspection_summary.md
```

## Stage 2 security invariants

1. Reject absolute, drive, UNC, NUL, and `..` archive paths.
2. Reject symlinks and special Unix file types.
3. Enforce configurable file-count, byte, and compression-ratio limits.
4. Stream extraction through temporary files and enforce runtime byte caps.
5. Never recursively extract nested archives.
6. Never evaluate spreadsheet formulas.
7. No LLM calls and no network access during inspection.

## Non-negotiable decision invariants

1. **Evidence** — every `ExplicitFact` references at least one `EvidenceSpan`.
2. **Money** — amounts use `Decimal`; Python `float` input is rejected; JSON must not introduce binary float artifacts.
3. **Calculations** — every `CalculatedValue` carries a deterministic `CalculationTrace` with input records.
4. **Versions** — final applicability is selected by a deterministic resolver (not an LLM).
5. **Proof** — every completed decision proof references applicable versions, evidence-backed facts, calculations, and rule IDs/versions as required by `ProofBundle` validation.
6. **LLM restrictions** — LLMs must not perform final monetary calculation or final document-version applicability selection.

## Stage boundaries

- Stage 1: contracts and domain models only.
- Stage 2: safe archive inventory + schema profiling only. Documents are inventoried, not content-parsed.
