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
| Retrieval | local lexical/vector (frozen) | PostgreSQL FTS + postgres_numpy_exact (optional pgvector) |
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

## Stage 4 data flow

```text
Stage 3 parse output
  → StructureAwareChunker (parent / child / table / atomic)
  → pinned SentenceTransformer embeddings (multilingual-e5-small, 384-d; FULL default)
  → FULL: PostgreSQL FTS (simple, OR lexemes)
       + postgres_numpy_exact (BYTEA float32 + exact NumPy cosine)
       OR optional pgvector when extension already installed
       + shared RRF
  → optional cross-encoder rerank (disabled by default; large-model approval required)
  → FAST (frozen): SQLite FTS5 + float32 local vectors + shared RRF
  → chunks.jsonl + chunk_manifest.json + index_report.json + retrieval_summary.md
```

Docker is never required for runtime. Compose/Dockerfile files are optional references only.

Stage 4.3 verified the competition path against local PostgreSQL 18.4 with `postgres_numpy_exact` (pgvector absent and not required). BGE-M3 and the BGE reranker remain optional large models — disabled and not verified.

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

## Stage 4 retrieval invariants

1. Every chunk retains at least one evidence span; synthetic retrieval text is not primary evidence.
2. Hard filters precede ranking (OR within field, AND across fields).
3. Shared RRF fusion for FAST and FULL; no silent hybrid→lexical downgrade.
4. Exact pgvector cosine search by default; HNSW not required and not claimed.
5. Model revisions pinned in `model-lock.json`; offline after explicit prewarm.
6. Base package imports without Docling / SQLAlchemy / sentence-transformers.

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
- Stage 4: structure-aware chunking and hybrid retrieval only. No DeepSeek, fact extraction, version resolver, or workers.
- Stage 5A + 5A′ / 5A.1–5A.3: preflight quarantine, sanitized-manifest solver, baseline submission, isolated training scorer, shared PostParseQualityGate with page visual metadata (KNOWN|UNKNOWN), parse-cache v2 gate identity.
- Stage 5A.4 / 5A.4.1: selective provenance-safe OCR for blocking pages (Tesseract CLI eng+rus+kaz offline-ready after probe fix). No covenant calculation, no DeepSeek.
- Stage 5B: deterministic scenario/entity routing (template→account→txn→document). No covenant DSL, no authority/version selection, no DeepSeek.
- Stage 5C+: document taxonomy/authority, model gateway, extraction, decisions, calculated submission (not started).

## Stage 5A.2 data flow

```text
raw dataset root
  → preflight composition root (halyk_agent.preflight.service)
      package __init__ does not eagerly import discover/quarantine/service
      quarantine answer keys (filename or content shape)
      emit SanitizedDatasetManifest + preflight_manifest.json
  → competition solver (halyk_agent.solver)
      accepts ONLY SanitizedDatasetManifest (no dataset-root argument)
      require_audited_opener (FileOpener.opened_paths mandatory; fail closed)
      validate allowlist vs quarantine before reads
      opens allowlisted template / ledger / cases via RecordingFileOpener
      stage outputs then atomically publish after open audit
      schema-valid baseline submission (NULL_FIELDS)
      run_manifest.json lists solver opens only

training (HALYK_MODE=training only):
  submission + ground_truth → Decimal scorer → score_report.json
  (training package must never be imported by solver)
```

## Stage 5A.2 parsing trust

```text
pypdf PageVisualSignals (page.images / XObject) ──┐
Docling pictures[].prov[].page_no (or UNKNOWN) ──┼→ finalize → PostParseQualityGate
Protocol parse() / app batch / cache hit ────────┘

Authoritative mapping (trusted_usable = usable text AND non-blocking state):
  trusted usable pages, no blocking → SUCCESS
  ≥1 trusted usable page + ≥1 blocking page → PARTIAL
  every page blocking, or zero trusted usable pages → FAILED
  text on a blocking page never counts as trusted
  UNKNOWN visuals + empty/low text → conservative non-SUCCESS
  verified image_count>=1 + empty text → OCR_REQUIRED (blocking)

Blocking predicate (single source of truth):
  domain.page_quality.is_blocking_page_quality / BLOCKING_PAGE_QUALITY_STATES

Parse cache v2 identity includes:
  cache_schema_version, parser backend/version, canonical schema,
  page_quality_gate_version=halyk.page_quality_gate.v2,
  page_quality_configuration_hash=page-quality-visual-v2,
  OCR policy/backend/config, source sha256

Legacy / pre-visual cache entries → CACHE_INCOMPATIBLE (reparse; never silent SUCCESS)
```

## Stage 5A.2 isolation invariants

1. Default `HALYK_MODE=competition`.
2. Solver must never import `halyk_agent.training` or preflight quarantine/discovery/service modules at runtime (subprocess `sys.modules` regression covers this).
3. Competition solver consumes only a sanitized manifest and never opens, deserializes, or receives answer-key content; raw-dataset preflight may inspect candidate JSON solely for quarantine classification.
4. Present / deleted / corrupt / renamed answer-key variants yield byte-identical `submission.json`, **and** recorded solver opens exclude quarantine paths.
5. Quarantined paths may appear in `preflight_manifest.json` as metadata only; never in `run_manifest.json`.
6. Scorer never feeds expected values back into solver artifacts.
7. No parser backend publishes final trusted SUCCESS without `PostParseQualityGate`.
8. Unaudited file openers (missing `opened_paths`) are rejected before reads or submission publish.

## Stage 5A.4 selective OCR

```text
parse_report + documents
  → plan_selective_ocr (is_blocking_page_quality only; max_pages bound)
  → ocr probe (TESSERACT_CLI preferred; no download; no silent RapidOCR fallback)
  → recognize_pages(selected only)
  → validate_ocr_page_text
  → merge_ocr_into_document (preserve embedded; TextOrigin.OCR blocks)
  → PostParseQualityGate
  → enriched parse outputs (separate directory)
```

Evidence spans from OCR blocks carry `text_origin=OCR`, backend identity, page number, exact quote.

## Stage 5B / 5B.1 / 5B.2 scenario and entity routing

```text
SanitizedDatasetManifest
  → canonicalize allowlist + quarantine
  → audited FileOpener reads (template + ledger only)
  → OCR-enriched CanonicalDocuments + evidence catalogue
  → scenario universe (template)
  → txn-id anchors + account-id fallback
  → document precedence:
       1 EXPLICIT_ACCOUNT_ID
       2 EXPLICIT_BORROWER_DECLARATION
       3 GROUP_SEGMENT_DECLARATION (anchored identity + strong relation)
       4 NORMALIZED_LEGAL_NAME (identity_key via whitespace-normalized search)
       5 UNRESOLVED
  → RoutingManifest + JSONL + identity_evidence.jsonl
       (document_span + ledger_row provenance; source_sha256 required for docs)
  → open audit → staged publish (replace after staged validation)
```

### Whitespace-normalized matching (5B.2)

Matching collapses Unicode whitespace runs to one ASCII space while mapping every
normalized index back to raw offsets. Evidence quotes remain exact raw substrings
(`quote == raw_text[start:end]`), including embedded newlines. A layout hit still
requires exact `identity_key` equality.

### Group / segment relations (5B.2)

Bare nouns (`group` / `группа` / `топ` / `segment` / `сегмент`) are insufficient.
LEVEL 3 requires an exact anchored borrower identity and a strong structural
predicate in a bounded local window (subsidiary / conducted through / дочерн* /
входит в группу / …).

### Routing vs Stage 5C

- **Routing (5B):** which scenario/entity a document *concerns*.
- **Stage 5C (not started):** whether that document is relevant, authoritative, or current.

### Legal-name keys

- `identity_key` — ACCEPT match; preserves legal-form class (`jsc` ≠ `llp` ≠ `too`).
- `base_key` — candidate/conflict diagnostics only; **never** sufficient to accept a link.
- Language variants of the same form (e.g. `JSC` / `АО`) may share a form class deliberately.

### Leakage boundary

Routing reuses the shared `halyk_agent.dataset_access` contract (same allowlist/quarantine
canonicalization and audited opener rules as the Stage 5A solver). Role labels alone are
never trusted.
