# Stage 6 — Evaluation Contract

Status: **IMPLEMENTED**. The implementation began on `stage-6/covenant-evaluator` and is consumed by `stage-7/solver-integration`.

Stage 6 is the strict deterministic calculation kernel. It consumes the typed Stage 5D covenant AST and Stage 5F calculation inputs; it does not read training answers, call an LLM, or invent missing financial facts.

## Core contract

- `CovenantDefinition -> EvaluationPlan` is deterministic.
- `PlanStructureValidator` runs before data binding.
- `ContextValidator` runs after Stage 5F binding.
- Every declared dependency/root must exist; duplicate IDs, missing dependencies, cycles, invalid payloads/types/modifiers and orphan nodes fail closed.
- Stage 5D/5F manifest/content hashes are verified before publication.
- Stage 5F `amount_contract_version`, selector coverage/readiness, scenario ownership and input IDs are enforced.
- FLOW / AS_OF period membership is tri-state: undecidable is `UNRESOLVED`, never silently false.
- `include_flags` / `exclude_flags` are applied explicitly.
- No implicit FX conversion is permitted.

## Materiality

`MATERIALITY_FLOOR` carries a typed MONEY threshold and optional target category. A deterministic filter that removes every relevant row yields a genuine zero for `SUM`; any undecidable relevant predicate yields `UNRESOLVED`.

The upstream covenant money parser remains fail-closed: complete monetary tokens only, no OCR digit repair, all matched instructions reconciled, conflicting typed floors rejected.

## Decimal policy

Execution uses an explicit local Decimal context:

- precision: 60;
- rounding: `ROUND_HALF_EVEN`;
- no float;
- no pre-comparison quantization unless a covenant explicitly requires rounding.

Zero denominator is `ERROR`. A negative denominator is evaluated faithfully and emits `NEGATIVE_DENOMINATOR`.

## Springing activation — competition-grounded semantics

`CASE.ru.md` resolves the earlier policy ambiguity: even when a springing covenant is inactive, the submission `actual` must remain the **main covenant metric**, not the activation metric and not `null`.

Therefore Stage 6 now:

1. evaluates the activation subgraph;
2. records `activation_state=INACTIVE` when the condition is false;
3. still evaluates the main metric so that the required `actual` exists;
4. returns internal `NOT_ACTIVATED` only as an internal state marker;
5. leaves the final binary competition mapping to the Stage 7 submission adapter, which maps an evaluable inactive covenant to `COMPLIANT`.

Main-metric `ERROR` / `UNRESOLVED` is still surfaced even when activation is inactive; Stage 6 does not hide missing or invalid data.

## Strict/public expectations

On the supplied public corpus, the strict layer currently resolves 29 of 36 cells and leaves 7 unresolved, principally because strict Stage 6 refuses unsupported cross-scenario FX generalization and one structural GROUP_CAPEX source gap remains. These strict results are preserved separately from Stage 8 competitive fallbacks.

## Non-goals

- no Stage 8 FX/PPE fallback inside Stage 6;
- no invented ownership or GROUP_CAPEX;
- no ground-truth / answer-key reads;
- no LLM calls;
- no heuristic `evidence_txn_id` selection.
