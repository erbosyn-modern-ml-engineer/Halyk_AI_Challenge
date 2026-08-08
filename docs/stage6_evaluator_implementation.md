# Stage 6 deterministic covenant evaluator

This note records the implementation map for the Stage 6 calculation kernel. It
is deliberately narrower than a generic workflow engine: Stage 5D already
supplies the typed covenant AST and Stage 5F already supplies row-level,
calculation-ready inputs.

## Donor implementations studied

The implementation was informed by four pinned open-source revisions. They are
references, not new runtime dependencies.

| Project | Revision / files studied | Adapted idea |
|---|---|---|
| Apache Hamilton | `b0a2abd46ae30e6f05bafaecbb513ba19aae17a7` — `hamilton/graph.py`, `hamilton/execution/graph_functions.py` | Dependency DAG traversal, requested-subgraph execution and trace thinking. Halyk intentionally rejects missing dependencies instead of treating them as external inputs. |
| MetricFlow | `65b9d4244fb79c6ff431fa8ed49fac3f435ab7f3` — `metricflow/dataflow/dataflow_plan.py`, `metricflow/dataflow/builder/dataflow_plan_builder.py` | Separation of semantic representation from immutable validated execution plan. No SQL/data-warehouse layer is imported. |
| Capitec DSP Decision Engine | `96889ca5f6b60711a692c93b9c38f02702afb5bf` — `decider/modules/expression.py`, `decider/graphutil.py`, `decider/executor.py` | Typed node/static/external-input representation, deterministic Kahn topological ordering, compile/execute separation. Halyk replaces dataframe semantics with typed Decimal financial values. |
| OpenFisca Core | `4f7f09833afe7e8b6856e8d7a3016c04a931009b` — `openfisca_core/periods/period_.py` | Behavioral reference only for strict period semantics and calculation tracing. No OpenFisca source code is copied or imported because the project is AGPL-3.0. |

Hamilton and MetricFlow are Apache-2.0; the inspected Capitec DSP Decision Engine
revision is MIT. The Stage 6 source is a Halyk-specific implementation rather
than vendored framework code.

## Halyk execution pipeline

```text
CovenantDefinition (Stage 5D)
        -> EvaluationPlanner
        -> immutable EvaluationPlan DAG
        -> PlanStructureValidator
        -> Stage 5F artifact/hash binding
        -> ContextValidator
        -> Decimal EvaluationExecutor
        -> CovenantEvaluationResult + deterministic trace
```

The evaluator does not call an LLM, does not perform retrieval, does not infer
foreign-exchange rates, and does not read training answers.

## Fail-closed invariants

- Every dependency and root must exist; duplicate IDs, cycles and orphan nodes
  are rejected before execution.
- The Stage 5F amount contract and artifact hashes must match before any output
  is published.
- Selector ownership/readiness, scenario universe, periods and currencies are
  validated before arithmetic.
- `include_flags` and `exclude_flags` are applied explicitly at Stage 6 even
  though the older Stage 5F semantic matcher does not consume those fields.
- A decidable materiality filter that removes every row yields a true zero for
  `SUM`; an undecidable predicate yields `UNRESOLVED`.
- Arithmetic runs in a local Decimal context with precision 60 and
  `ROUND_HALF_EVEN`; ambient process precision and floats are irrelevant.
- No implicit FX conversion is permitted.
- Zero denominator is `ERROR`; negative denominator is evaluated faithfully and
  emits `NEGATIVE_DENOMINATOR`.
- Springing activation is evaluated as its own dependency subgraph. If the
  condition is inactive, main-metric failures are not evaluated or allowed to
  contaminate the result.

## Published artifacts

`halyk_agent.app.evaluation.evaluate_from_paths` stages output and publishes only
on successful global validation:

- `evaluation_manifest.json`
- `evaluation_plans.jsonl`
- `covenant_evaluations.jsonl`
- `evaluation_summary.md`

The manifest binds the exact Stage 5D covenant manifest, Stage 5F manifest,
calculation inputs, selector coverage and definition readiness hashes, plus
hashes of the generated plans and results.

## Intentionally unresolved policy edge

An inactive springing covenant is represented internally as `NOT_ACTIVATED` with
`activation_state=INACTIVE`. Stage 6 does **not** guess whether the competition's
submission schema wants that state serialized as `COMPLIANT` or handled another
way. That mapping belongs in solver/submission integration and must be grounded
in the case specification before publication.
