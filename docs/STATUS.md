# Status

Current branch: `stage-7/solver-integration`

Current engineering state: **Stages 6, 7 and 8 implemented; Stage 9 reproduction tooling implemented.**

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

## Stage 8 — bounded competitive fallbacks

Strict Stage 6 output is preserved separately. Stage 8 is an explicitly labeled competition layer used only for strict non-resolved cells because a blank scores the same as an incorrect answer.

Public-corpus fresh run before fallbacks:

- 36 covenant cells;
- strict Stage 6: 29 resolved / 7 unresolved / 0 errors;
- Stage 5F after narrow damaged-related-party recovery: 35 READY / 1 UNRESOLVED;
- source reads: 204;
- fact extraction model calls: 0.

Bounded public-corpus fallbacks:

1. **EUR/USD settlement-rate fallback** — derives the unique source-backed settlement ratio when exactly one non-conflicting ratio exists. On the supplied public corpus this ratio is `1.16`. Strict Stage 6 still refuses cross-scenario FX generalization; only the competitive fallback uses it.
2. **P5 GROUP_CAPEX fallback** — reconstructs the Note 7 PPE roll-forward residual only when opening NBV, depreciation, closing NBV and zero disposals are source-backed and no competing movement class is named. Public residual: `21,847,362.55 USD`.

The public end-to-end competitive run fills all 36 template cells. Fallback outputs are separately diagnosable in `fallback_cells.jsonl` and must not be described as strict/source-authoritative Stage 6 results.

## Stage 9 — fresh-run reproduction

Implemented:

- runtime-path-independent dataset identity;
- runtime-duration-independent parsed input identity;
- `halyk-agent reproduce-compare` for two independent completed runs;
- byte comparison of submission and fallback diagnostics;
- stable pipeline-manifest comparison excluding the intentional `run_id` field;
- mandatory `ground_truth_access == none` check.

Before the final lineage-normalization patch, two independent public runs already produced byte-identical submission and fallback diagnostics. The post-normalization full duplicate run still needs one successful two-run execution in a fresh environment before claiming full end-to-end lineage determinism.

## Current quality gate

GitHub Actions / Ubuntu 24.04 / Python 3.12:

- Ruff format: pass;
- Ruff lint: pass;
- mypy: **251 source files, 0 issues**;
- pytest: **789 passed, 22 skipped, 0 failed**.

The CI workflow is read-only and installs the repository's `full` and `retrieval-full` extras.

## Next

1. Run two independent post-lineage public solves and `reproduce-compare`.
2. Run one final fresh public-corpus submission reproduction from the branch HEAD.
3. Hand the finished branch to Claude Opus 5 for red-team review.
4. Do not merge to `main` until red-team findings are triaged.
