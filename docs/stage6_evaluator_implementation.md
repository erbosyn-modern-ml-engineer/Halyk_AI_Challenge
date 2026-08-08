# Stage 6 deterministic covenant evaluator

Stage 6 is a Halyk-specific deterministic financial evaluation kernel. Stage 5D supplies the typed semantic AST; Stage 5F supplies calculation-ready inputs.

## Donor implementations studied

These are references, not runtime dependencies:

| Project | Pinned revision | Adapted idea |
|---|---|---|
| Apache Hamilton | `b0a2abd46ae30e6f05bafaecbb513ba19aae17a7` | dependency DAG traversal, requested-subgraph execution, trace thinking |
| MetricFlow | `65b9d4244fb79c6ff431fa8ed49fac3f435ab7f3` | semantic representation -> immutable validated execution plan |
| Capitec DSP Decision Engine | `96889ca5f6b60711a692c93b9c38f02702afb5bf` | typed node/static/external-input concepts and deterministic topological execution |
| OpenFisca Core | `4f7f09833afe7e8b6856e8d7a3016c04a931009b` | behavioral reference only for strict period/calculation semantics; AGPL source was not copied/imported |

Hamilton/MetricFlow are Apache-2.0; the inspected Capitec revision is MIT. OpenFisca is AGPL-3.0 and was used only as a clean-room behavioral reference.

## Pipeline

```text
CovenantDefinition
  -> EvaluationPlanner
  -> immutable EvaluationPlan DAG
  -> PlanStructureValidator
  -> Stage 5F artifact/hash binding
  -> ContextValidator
  -> Decimal EvaluationExecutor
  -> CovenantEvaluationResult + deterministic trace
```

## Important invariants

- every dependency/root exists;
- no cycles or orphan execution nodes;
- Stage 5F ownership/readiness/period/currency contracts validated before arithmetic;
- explicit selector flags honored;
- true post-filter empty set -> zero; undecidable filter -> `UNRESOLVED`;
- local Decimal precision 60 / `ROUND_HALF_EVEN`;
- no implicit FX;
- zero denominator `ERROR`;
- negative denominator faithful + diagnostic.

## Springing execution

The competition specification requires the main `actual` even when activation is false. Activation is therefore evaluated first, but an inactive state no longer short-circuits the main metric. Stage 6 computes the main actual and records internal `NOT_ACTIVATED` / `INACTIVE`; Stage 7 maps that evaluable internal state to submission `COMPLIANT`.

## Published evaluator artifacts

- `evaluation_manifest.json`
- `evaluation_plans.jsonl`
- `covenant_evaluations.jsonl`
- `evaluation_summary.md`

The manifest binds Stage 5D/5F identities plus generated plan/result hashes.
