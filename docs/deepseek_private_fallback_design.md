# DeepSeek bounded semantic fallback — private-data design

## Purpose

Use an LLM only where it can improve generalization to unseen private documents, while preserving deterministic financial arithmetic and causal evidence.

## Model/API

The intended provider is DeepSeek through its OpenAI-compatible API. Runtime configuration must be environment-only (`DEEPSEEK_API_KEY`); secrets are never committed.

## Allowed LLM tasks

The model may propose structured candidates only for inputs that deterministic extraction marks `UNRESOLVED` or ambiguous, for example:

- covenant comparator / threshold / period / scope candidates from unseen wording;
- transaction category aliases when deterministic rules cannot classify a potentially relevant row;
- document relation / entity relation candidates;
- semantic mapping of a source-backed treatment or exception.

## Forbidden LLM tasks

The model must never directly decide:

- final `status`;
- final `actual`;
- `evidence_txn_id`;
- FX rates not explicitly grounded in source evidence;
- ownership / related-party facts not grounded in supplied documents.

## Acceptance gate

Every LLM candidate is treated as untrusted input:

1. parse strict structured JSON;
2. validate against the typed domain model;
3. require exact source quote/span provenance;
4. run existing Stage 5F readiness / conflict checks;
5. run Stage 6 deterministic evaluation;
6. for evidence, run Stage 10.5 counterfactual replay;
7. reject on ambiguity, type mismatch, missing provenance, or inconsistent replay.

## Failure semantics

429/5xx/timeouts/invalid JSON never produce a guessed answer. The system falls back to the deterministic unresolved path. LLM output must be cacheable by source hash + prompt/schema version so repeated runs remain auditable.

## Why not use an LLM inside evidence selection

Competition evidence is an executable causal condition: removing/reverting one transaction must change the verdict. Stage 10.5 already proves this with deterministic replay. An LLM can help propose semantic treatments upstream, but causal publication remains an exact computation.
