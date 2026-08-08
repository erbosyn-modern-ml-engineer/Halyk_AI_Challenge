# Status

Current stage: **6 PRE-FLIGHT — Final Materiality Root-Closure**
Stage status: **IN PROGRESS on branch** `stage-6-preflight/materiality-final-closure`
Source: materiality-safety-fix @ `f8bac002a8d9f42c1abfce8231794cd04030df21`

## Why this patch exists

Opus 5 MAX review of materiality-safety-fix returned `BLOCKERS = 0` but `HIGH = 3`.
Stage 6 evaluator must not start until these final fail-open roots are closed:

| Finding | Closure |
|---------|---------|
| HIGH-1 | Instruction region covers full regex match + end-sentence (legal abbrev safe) |
| HIGH-2 | Reconcile **all** materiality/add-back instructions (no first-match-wins) |
| HIGH-3 | Complete monetary numeric token grammar (space groups; no prefix truncation) |

Design freeze: `docs/stage6_evaluation_contract.md`

## Pipeline map

| Stage | Question |
|-------|----------|
| **5B** | Which scenario owns each ledger row? |
| **5C** | What type is it, and is it authoritative for a fact domain? |
| **5D** | What covenant definition/selectors/modifiers apply? |
| **5E** | What trusted structured facts exist in authoritative sources? |
| **5F** | What calculation-ready transaction/adjustment inputs feed Stage 6? |
| **6 PRE-FLIGHT** | Close evaluation + materiality safety contracts |
| **6** (next) | Covenant actuals / compliance (**not started**) |

## Non-goals (hard stop)

- No EvaluationPlanner / EvaluationExecutor / evaluate CLI
- No Stage 7
- No OCR glyph auto-correction
- No push/merge until final Opus Stage 6 preflight signoff

## Next

Claude Opus 5 final targeted re-review of ONLY:
HIGH-1, HIGH-2, HIGH-3.
