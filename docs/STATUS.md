# Status

Current stage: **6 PRE-FLIGHT — Materiality Safety Closure**
Stage status: **IN PROGRESS on branch** `stage-6-preflight/materiality-safety-fix`
Source: independently audited Stage 6 preflight lineage @ `327e7ae69cb89fb898354024b947d0a78da0f794` on `main`

## Why this patch exists

Independent engineering audit found two HIGH fail-open defects in materiality
threshold parsing. Stage 6 must not start until both are closed, because
`CovenantModifier.threshold` will be trusted numeric input.

| Finding | Closure |
|---------|---------|
| HIGH-A | Malformed/OCR-corrupted money tokens fail closed (no numeric-prefix truncation) |
| HIGH-B | Distinct materiality floors in one instruction → ambiguity / no published floor |

Prior pre-flight (evaluation contract closure) remains on `main` via merge of
`stage-6-preflight/evaluation-contract-closure`.

Design freeze: `docs/stage6_evaluation_contract.md`

## Pipeline map

| Stage | Question |
|-------|----------|
| **5B** | Which scenario owns each ledger row? |
| **5C** | What type is it, and is it authoritative for a fact domain? |
| **5D** | What covenant definition/selectors/modifiers apply? |
| **5E** | What trusted structured facts exist in authoritative sources? |
| **5F** | What calculation-ready transaction/adjustment inputs feed Stage 6? |
| **6 PRE-FLIGHT** | Close evaluation input/validation + materiality safety contracts |
| **6** (next) | Covenant actuals / compliance (**not started**) |

## Non-goals (hard stop)

- No EvaluationPlanner / EvaluationExecutor / evaluate CLI
- No Stage 7
- No inventing FX / OCR auto-correction of money glyphs
- No push/merge until Opus materiality signoff

## Next

Claude Opus 5 targeted re-review of ONLY:
HIGH-A (malformed money) and HIGH-B (materiality ambiguity).
