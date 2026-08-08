# Status

Current stage: **6 PRE-FLIGHT — Evaluation Contract Closure**
Stage status: **IN PROGRESS on branch** `stage-6-preflight/evaluation-contract-closure`
Source: independently approved Stage 5F.3 @ `b452206cf20ca428a9f44915219378ac47f488cb` on `main`

## Why this patch exists

Claude Opus 5 architecture review of Stage 6 returned `TARGETED_DESIGN_FIX_REQUIRED`.
This pre-flight closes upstream contract defects **before** any covenant evaluator exists:

| Finding | Closure |
|---------|---------|
| BLOCKER-1 | Typed `MATERIALITY_FLOOR.threshold` (+ optional category) |
| BLOCKER-2 | `selector_coverage_hash` / `definition_readiness_hash` |
| HIGH-1 | `InputPeriodSemantics` FLOW vs AS_OF on `CalculationInput` |
| HIGH-2 | Source-backed temporal fields for P4 FLOW / P8 AS_OF |
| HIGH-3 | Document mixed-currency numeric unreadiness (29, not 34) |
| HIGH-4 | Split `PlanStructureValidator` vs `ContextValidator` |

Design freeze: `docs/stage6_evaluation_contract.md`

## Pipeline map

| Stage | Question |
|-------|----------|
| **5B** | Which scenario owns each ledger row? |
| **5C** | What type is it, and is it authoritative for a fact domain? |
| **5D** | What covenant definition/selectors/modifiers apply? |
| **5E** | What trusted structured facts exist in authoritative sources? |
| **5F** | What calculation-ready transaction/adjustment inputs feed Stage 6? |
| **6 PRE-FLIGHT** | Close evaluation input/validation contracts |
| **6** (next) | Covenant actuals / compliance (**not started**) |

## Non-goals (hard stop)

- No EvaluationPlanner / EvaluationExecutor / evaluate CLI
- No Stage 7
- No inventing FX / dates / OCR identity
- No push/merge until Opus re-review of the six findings

## Next

Claude Opus 5 XHIGH targeted re-review of ONLY:
BLOCKER-1, BLOCKER-2, HIGH-1, HIGH-2, HIGH-3, HIGH-4.
