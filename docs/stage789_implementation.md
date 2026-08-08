# Stages 7–9 — competition integration, bounded fallback and reproduction

## Stage 7 — production solver integration

The real competition path is fixed and explicit rather than delegated to a generic orchestration framework:

```text
SanitizedDatasetManifest
  -> audited source copies
  -> parallel FAST parsing
  -> selective OCR on blocking pages only
  -> scenario routing
  -> authority classification
  -> covenant compilation
  -> deterministic structured facts
  -> Stage 5F transaction semantics
  -> strict Stage 6 evaluation
  -> exact submission adapter
```

The runner never needs `ground_truth.json` and the final pipeline manifest records `ground_truth_access=none`.

### Orchestration donor reconnaissance

Two pinned Apache-2.0 projects were studied as reference-only implementations:

- DVC `56e59829512ff134aa269099a2099587b810b4dd`, especially `dvc/repo/reproduce.py` — explicit stage dependency/reproduction thinking.
- Kedro `7c8ec55bab549f7499e06c50a8b82db390347dec`, especially `kedro/pipeline/pipeline.py` — explicit node input/output/dependency composition.

No DVC/Kedro runtime dependency or source code was copied. A fixed Halyk pipeline is smaller and easier to audit for this competition.

### Submission semantics

The Stage 7 adapter enforces the supplied case contract:

- exact template universe and keys;
- only `COMPLIANT` / `BREACH` statuses;
- inactive springing + evaluable main actual -> `COMPLIANT`;
- positive-magnitude `actual`, rounded to two decimals only at serialization;
- causal evidence only. A transaction ID is emitted only when undoing one explicit authoritative Stage 5F treatment uniquely flips the binary verdict.

## Stage 8 — competitive fallback layer

Stage 8 is intentionally separated from strict Stage 6.

### Narrow related-party recovery

A damaged owner token may be recovered only when the explicit corrupted token pattern uniquely matches one normalized ledger counterparty identity and one qualifying owner in the same scenario. No edit-distance or fuzzy legal-form guessing is used.

On the public corpus this closes the P6 structural identity gap; Stage 5F becomes 35 READY / 1 UNRESOLVED.

### EUR/USD settlement fallback

Strict Stage 6 continues to refuse unsupported FX. Competitive fallback may derive a rate only when source-backed settlement references yield exactly one plausible non-conflicting EUR/USD ratio. The public corpus yields `1.16`.

This is an explicit competition heuristic, not a claim that the strict covenant source authorizes cross-scenario FX generalization.

### P5 PPE roll-forward fallback

The fallback computes a residual GROUP_CAPEX only when a single qualifying PPE note supplies opening NBV, depreciation, closing NBV and zero disposals and names no competing movement class such as acquisition, transfer, revaluation, impairment or FX movement.

Public residual: `21,847,362.55 USD`.

Fallback decisions are written separately to `fallback_cells.jsonl`.

## Stage 9 — reproduction

`halyk-agent reproduce-compare` compares two independently completed runs:

- byte-identical `submission.json`;
- byte-identical fallback diagnostics;
- stable pipeline-manifest fields (excluding the deliberate `run_id`);
- identical evaluation-manifest identity;
- zero ground-truth access.

Routing and parsed-input identities intentionally ignore runtime absolute paths and parser duration/host entropy while remaining bound to stable source and semantic-content hashes.

Two independent runs before the final lineage normalization already proved byte-identical submission/fallback outputs. A post-normalization two-run replay is the last reproduction check before red-team signoff.

## Validation

Current GitHub Actions gate after Stage 7–9 integration:

- Ruff format: pass;
- Ruff lint: pass;
- mypy: 251 source files / 0 issues;
- pytest: 789 passed / 22 skipped / 0 failed.
