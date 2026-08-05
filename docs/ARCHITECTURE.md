# Architecture

## Shared domain core

`halyk_agent.domain` holds the competition-invariant models shared by FAST and FULL:

- evidence spans and facts
- transactions and calculated values (Decimal only)
- rule references and decisions
- applicable version sets and proof bundles
- dataset manifests and schema profiles (Stage 2)
- canonical documents, pages, blocks, tables, parse metrics (Stage 3)

Domain code must not import contracts, adapters, profiles, FastAPI, SQLAlchemy, Redis, LangGraph, Docling, or provider SDKs.

## Profiles

| Concern | FAST | FULL |
|---------|------|------|
| Storage | local / SQLite or memory | PostgreSQL |
| Jobs | direct asyncio | Redis lease + heartbeat + recovery |
| Parsing | pypdf (FAST) | pypdf pre-pass + Docling fallback |
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

## Stage 3 data flow

```text
Stage 2 inspection directory
  → select DOCUMENT / supported formats
  → FAST: pypdf (PDF/TXT)
  → quality gate
  → FULL: Docling fallback only when needed (or --force-docling)
  → CanonicalDocument (pages / blocks / tables)
  → exact EvidenceSpan catalog
  → content-addressed local JSON parse cache
  → documents/*.json + evidence_catalog.jsonl + parse_report.json + parsing_summary.md
```

## Stage 2 security invariants

1. Reject absolute, drive, UNC, NUL, and `..` archive paths.
2. Reject symlinks and special Unix file types.
3. Enforce configurable file-count, byte, and compression-ratio limits.
4. Stream extraction through temporary files and enforce runtime byte caps.
5. Never recursively extract nested archives.
6. Never evaluate spreadsheet formulas.
7. No LLM calls and no network access during inspection.

## Stage 3 parsing invariants

1. FAST never imports Docling.
2. Canonical bounding boxes use TOP_LEFT origin only.
3. Block offsets are half-open and must equal exact page raw substrings.
4. Evidence spans are exact quotes — no fuzzy alignment.
5. Parse cache keys include source hash, parser package/version, config hash, schema and normalization versions.
6. PyMuPDF / fitz is intentionally excluded (AGPL).
7. OCR defaults to disabled; do not claim scanned-PDF production support unless proven.

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
- Stage 3: document parsing to canonical evidence spans. No embeddings, retrieval, LLM extraction, or workers.
