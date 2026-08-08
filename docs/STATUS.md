# Status

Current branch: `stage-7/solver-integration`

Current engineering state: **Stages 6, 7, 8 and 9 implemented. Ready for Claude Opus 5 red-team review.**

Base: final Stage 6 pre-flight closure @ `86bb9908b789a9026c64a5d2a09d95b431d242ee`.

## Stage 6 — deterministic covenant evaluator

Implemented:

- typed `CovenantDefinition -> EvaluationPlan` planning;
- strict DAG / type / modifier validation;
- Stage 5F context validation and artifact-hash binding;
- FLOW / AS_OF period semantics;
- materiality-floor evaluation nodes;
- local Decimal context (`prec=60`, `ROUND_HALF_EVEN`), no float;
- no implicit FX;
- deterministic calculation trace;
- springing activation while still computing the main metric required by the competition contract.

Stage 6 remains strict/source-faithful. It does not use Stage 8 competitive fallbacks.

## Stage 7 — real competition solver integration

Implemented production path:

`sanitized manifest -> audited source copies -> parallel FAST parse -> selective OCR -> routing -> authority -> covenant compile -> fact extraction -> Stage 5F -> Stage 6 -> exact submission adapter`

Important competition-contract closures from `CASE.ru.md`:

- submission status is exactly `COMPLIANT` / `BREACH`;
- `actual` is always the main covenant metric, including inactive springing covenants;
- inactive springing covenant maps to submission `COMPLIANT` only after the main actual is computed;
- `evidence_txn_id` is causal only; largest/latest/threshold-crossing heuristics are forbidden;
- final submission `actual` is positive magnitude and rounded only in the submission layer.

The solver reads only sanitized allowlisted paths. `ground_truth.json` is neither required nor read.

A Stage 7 counterfactual-evidence audit found and closed one HIGH integration bug: the shared 36-definition Stage 5F context had been passed to a single-plan executor, causing every evidence replay to fail global-universe validation and collapse to `null`. Counterfactual replay is now definition/scenario scoped. On the public corpus this produces exactly one causal transaction evidence value: `B4 / 6.1 -> TXN-B4-0026`.

## Stage 8 — bounded competitive fallbacks

Strict Stage 6 output is preserved separately. Stage 8 is an explicitly labeled competition layer used only for strict non-resolved cells because a blank scores the same as an incorrect answer.

Public-corpus strict run before fallbacks:

- 36 covenant cells;
- strict Stage 6: 29 resolved / 7 unresolved / 0 errors;
- Stage 5F after narrow damaged-related-party recovery: 35 READY / 1 UNRESOLVED;
- source reads: 204;
- fact extraction model calls: 0.

Bounded public-corpus fallbacks:

1. **EUR/USD settlement-rate fallback** — derives the unique source-backed settlement ratio when exactly one non-conflicting ratio exists. On the supplied public corpus this ratio is `1.16`. Strict Stage 6 still refuses cross-scenario FX generalization; only the competitive fallback uses it.
2. **P5 GROUP_CAPEX fallback** — reconstructs the Note 7 PPE roll-forward residual only when opening NBV, depreciation, closing NBV and zero disposals are source-backed and no competing movement class is named. Public residual: `21,847,362.55 USD`.

The competitive public run fills all 36 template cells. Fallback outputs remain separately diagnosable in `fallback_cells.jsonl` and must not be described as strict/source-authoritative Stage 6 results.

## Stage 9 — fresh-run reproduction CLOSED

Implemented and exercised:

- runtime-path-independent sanitized-dataset identity;
- canonical ledger provenance independent of workspace/host path spelling;
- semantic parsed/OCR identity that excludes duration, temp/cache-byte, source-path and executable-path telemetry while remaining bound to parser/OCR configuration and semantic evidence content;
- `halyk-agent reproduce-compare` for two independent completed runs;
- byte comparison of submission and fallback diagnostics;
- stable pipeline-manifest comparison excluding only the intentional `run_id` field;
- identical evaluation-manifest identity;
- mandatory `ground_truth_access == none` check.

Two independent post-lineage public fresh runs completed successfully and `reproduce-compare` returned `deterministic=true`:

- source reads: `204 / 204`;
- ground-truth access: `none / none`;
- submission byte-identical: yes;
- fallback diagnostics byte-identical: yes;
- pipeline identity identical: yes;
- evaluation identity identical: yes;
- submission SHA-256: `66bfa70e8458ed95a17bd9c194d86693e4c4edf4566d3c66ff84f7e6494f5569`;
- fallback diagnostics SHA-256: `c3e71b0849db968a15d196afe8964ee7a35ebfb3047625656b862645aa47eb20`;
- evaluation manifest SHA-256: `a8242b91d4cbf29a080f047f8e092ea67f5e7889596cd4f0bed2b31b9e382413`.

Fresh-run debugging also exposed a reliability HIGH in OCR: PDFium rendering was being invoked from two OCR worker threads. PDFium wrapper calls are now serialized through a process-local lock while Tesseract subprocess work remains concurrent. A dedicated regression test enforces serialized rendering.

## Final quality gate before external red-team

GitHub Actions / Ubuntu 24.04 / CPython 3.12.13, read-only token:

- `ruff format --check .` — **387 files formatted**;
- `ruff check .` — **all checks passed**;
- `mypy src` — **252 source files, 0 issues**;
- `pytest -q` — **793 passed, 22 skipped, 0 failed**.

The CI workflow is read-only and installs the repository's `full` and `retrieval-full` extras. The two pytest warnings are upstream deprecations from Starlette/FastAPI and Docling, not test failures.

## Next

1. Give branch `stage-7/solver-integration` to Claude Opus 5 for final adversarial/red-team audit.
2. Require exact file/line evidence and executable counterexamples for BLOCKER/HIGH findings.
3. Triage and fix any real findings.
4. Do **not** merge to `main` until that red-team is complete.
