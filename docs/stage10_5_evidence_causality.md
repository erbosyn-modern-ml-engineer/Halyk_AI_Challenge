# Stage 10.5 — Counterfactual evidence causality

## Goal

Maximize `evidence_txn_id` accuracy without hard-coded public transaction IDs and without asking an LLM to guess causality.

The competition rule is executable: evidence is a unique transaction whose reclassification, inclusion/exclusion, correction, or absence changes the final covenant verdict. Candidate selection therefore uses counterfactual replay rather than size/recency/threshold-crossing heuristics.

## Donor reconnaissance

### PyWhy / DoWhy

- Repository: `py-why/dowhy`
- Pinned revision reviewed: `1d1efe77b092661252038baad72dc5d53e35ebfa`
- License: MIT
- Adapted principle: causal claims should be validated through explicit interventions/counterfactuals and falsifiable replay, not correlation or feature importance.
- No DoWhy runtime dependency or source code is copied.

### C3 — Contextual Counterfactual Credit Assignment

- Repository: `EIT-EAST-Lab/C3`
- Pinned revision reviewed: `628185becc70732771393be28d087e88f0a4a5e8`
- License: Apache-2.0
- Files/areas reviewed: `c3/credit/c3/`, especially the leave-one-out baseline / replay design described by the project.
- Adapted principle: freeze the surrounding execution context, remove one candidate cause, replay the same decision procedure, and attribute credit only to an executable counterfactual difference.
- No C3 runtime dependency or source code is copied.

## Halyk implementation

`src/halyk_agent/solver/evidence.py` now uses two deterministic layers:

1. **Authoritative-treatment replay** — undo source-backed Stage 5F reclassification, amount correction, period assignment or exclusion. If exactly one transaction flips the verdict, publish it.
2. **Fixed-context transaction-absence replay** — only if no treatment is uniquely causal, remove each transaction that actually contributed to the resolved Stage 6 result and replay the same `EvaluationPlan`. Publish only when exactly one removal flips the verdict.

If zero or multiple candidates flip the verdict, evidence remains `null`. The engine never chooses the largest, latest, nearest-to-threshold, or first matching transaction.

## Public validation

Ground truth is not available to the production solver. A separate training-only scorer was run only after `submission.json` had been produced.

On the public corpus after Stage 10.4 financial-ratio semantics:

- expected non-null evidence cells: **9**;
- exact evidence IDs before this change: **0 / 9**;
- exact evidence IDs after this change: **9 / 9**;
- `actual`: **36 / 36 exact**;
- `status`: **35 / 36 exact**;
- uniform public score: **35.00 / 36.00 = 97.22%**.

The sole remaining public scoring error is the separate P4/6.3 status/rounding-semantic edge; this evidence patch does not alter `actual` or covenant arithmetic.

## LLM boundary

An LLM may be useful on private data for semantic candidate generation (new clause wording, category aliases, entity relations), but it must not decide `evidence_txn_id` directly. Any LLM-proposed treatment/candidate is accepted only after deterministic counterfactual replay proves that the same Stage 6 plan changes verdict under the intervention.
