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

### Causal-evidence counterfactual isolation

A final Stage 7 audit found a HIGH bug in evidence replay: production holds a shared context for all 36 definitions, while `EvaluationExecutor.execute` validates a single-plan universe. Passing the shared context to a single-plan counterfactual therefore failed global validation and caused every candidate evidence transaction to disappear as `null`.

The replay path now scopes calculation inputs to the current scenario and selector/readiness state to the current definition before executing the counterfactual. A multi-definition regression test reproduces the old failure shape and verifies the repair. On the public corpus the final solver publishes exactly one non-null causal evidence transaction: `B4 / 6.1 -> TXN-B4-0026`.

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

The strict public Stage 6 run resolves 29/36 cells; Stage 8 fills the seven strict non-resolved cells without rewriting strict artifacts.

## Stage 9 — reproduction CLOSED

`halyk-agent reproduce-compare` compares two independently completed runs:

- byte-identical `submission.json`;
- byte-identical fallback diagnostics;
- stable pipeline-manifest fields excluding only the deliberate `run_id`;
- identical evaluation-manifest identity;
- zero ground-truth access.

### Stable lineage identity

The final lineage rules deliberately separate semantic identity from operational telemetry:

- sanitized input identity uses source hashes/roles and path-independent source names;
- ledger row provenance canonicalizes Windows/POSIX materialization paths to one stable source basename;
- routing identity excludes runtime workspace roots;
- authority parsed identity retains stable parser/OCR configuration, semantic evidence hash and stable counts but excludes parser durations, cache hits, OCR source paths, temp/cache byte counters and executable paths;
- canonical document/evidence hashes remain part of semantic downstream identity.

### OCR thread-safety reliability closure

Fresh reproduction exposed concurrent PDFium renders from two OCR workers. PDFium wrapper calls are not safe to run concurrently from multiple threads. Stage 9 therefore serializes only `_render_page_png` through a process-local lock. External Tesseract subprocesses remain concurrent, preserving most OCR parallelism without risking PDFium state corruption/crashes.

`tests/ocr/test_pdfium_thread_safety.py` exercises two concurrent OCR requests and asserts that PDFium render concurrency never exceeds one.

### Completed public reproduction

Two independent post-lineage public runs completed successfully:

- source reads: `204 / 204`;
- strict evaluation: `29 resolved / 7 unresolved / 0 errors` in each run;
- competitive unresolved cells after Stage 8: `0`;
- ground-truth access: `none / none`;
- submission byte-identical: yes;
- fallback diagnostics byte-identical: yes;
- pipeline identity identical: yes;
- evaluation identity identical: yes.

Hashes:

- submission: `66bfa70e8458ed95a17bd9c194d86693e4c4edf4566d3c66ff84f7e6494f5569`;
- fallback diagnostics: `c3e71b0849db968a15d196afe8964ee7a35ebfb3047625656b862645aa47eb20`;
- evaluation manifest: `a8242b91d4cbf29a080f047f8e092ea67f5e7889596cd4f0bed2b31b9e382413`.

`reproduce-compare` returned `deterministic=true` with an empty error list.

## Final validation before external red-team

GitHub Actions / Ubuntu 24.04 / CPython 3.12.13:

- Ruff format: 387 files formatted;
- Ruff lint: all checks passed;
- mypy: 252 source files / 0 issues;
- pytest: 793 passed / 22 skipped / 0 failed.

The workflow uses a read-only GitHub token. Remaining warnings are upstream deprecations from Starlette/FastAPI and Docling.
