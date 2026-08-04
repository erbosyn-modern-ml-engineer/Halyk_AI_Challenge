# Architecture

## Shared domain core

`halyk_agent.domain` holds the competition-invariant models shared by FAST and FULL:

- evidence spans and facts
- transactions and calculated values (Decimal only)
- rule references and decisions
- applicable version sets and proof bundles

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

Profile modules declare configuration only in Stage 1. Adapters are not implemented yet.

## Dependency direction

```text
app ──► profiles / contracts / domain
adapters ──► contracts / domain   (Stage 2+)
contracts ──► domain
domain ──► (stdlib + pydantic only)
```

## Non-negotiable decision invariants

1. **Evidence** — every `ExplicitFact` references at least one `EvidenceSpan`.
2. **Money** — amounts use `Decimal`; Python `float` input is rejected; JSON must not introduce binary float artifacts.
3. **Calculations** — every `CalculatedValue` carries a deterministic `CalculationTrace` with input records.
4. **Versions** — final applicability is selected by a deterministic resolver (not an LLM). Stage 1 only defines the result DTO.
5. **Proof** — every completed decision proof references applicable versions, evidence-backed facts, calculations, and rule IDs/versions as required by `ProofBundle` validation.
6. **LLM restrictions** — LLMs must not perform final monetary calculation or final document-version applicability selection.

## Stage 1 boundaries

Stage 1 establishes testable contracts and models only. No archive ingestion, parsers, workers, embeddings, retrieval, LangGraph graphs, or submission generation.
