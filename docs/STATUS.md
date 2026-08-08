# Status

Current stage: **6 — Deterministic Covenant Evaluator**
Stage status: **IMPLEMENTED on branch** `stage-6/covenant-evaluator`
Source: final materiality root-closure @ `86bb9908b789a9026c64a5d2a09d95b431d242ee`

## Stage 6 implementation

The pre-flight contract is now executable code. Stage 6 provides:

- deterministic `CovenantDefinition -> EvaluationPlan` planning;
- explicit dependency DAGs with strict missing-dependency / cycle / orphan rejection;
- `PlanStructureValidator` before binding;
- `ContextValidator` after Stage 5F binding;
- strict Stage 5D / Stage 5F artifact and content-hash verification before publication;
- FLOW / AS_OF period semantics with undecidable membership propagated as `UNRESOLVED`;
- materiality-floor filtering as an explicit evaluation node;
- local high-precision `Decimal` execution (`prec=60`, `ROUND_HALF_EVEN`), no float;
- no implicit FX conversion;
- zero denominator = `ERROR`;
- negative denominator = faithful calculation + `NEGATIVE_DENOMINATOR` diagnostic;
- lazy springing activation subgraph execution;
- deterministic result / calculation trace artifacts;
- standalone replay command `halyk-evaluate`.

Implementation map and donor provenance:
`docs/stage6_evaluator_implementation.md`.

## Validation

GitHub Actions on Ubuntu 24.04 / Python 3.12 after the Stage 6 implementation:

- `ruff format --check .` — pass;
- `ruff check .` — pass;
- `mypy src` — **245 source files, 0 issues**;
- `pytest -q` — **779 passed, 22 skipped, 0 failed**.

The same validation run installs both repository-supported optional stacks used by
the full suite: `full` (Docling) and `retrieval-full`.

The Linux CI run also exposed and closed an older cross-platform leakage-boundary
bug: Windows-style aliases of quarantined paths are now normalized before
allowlist/quarantine membership checks.

## Source-faithful expectations still to reproduce on the public corpus

The pre-flight freeze recorded the following expectations from the last known
Stage 5F.3 public artifacts. These are **not re-claimed as a fresh Stage 6 public
run until the gitignored artifacts/dataset are supplied and regenerated**:

- Stage 5D definitions: 36;
- Stage 5F structural READY / UNRESOLVED: 34 / 2;
- expected numerically evaluable definitions with current source data: 29;
- mixed currency must fail closed rather than invent FX.

No `ground_truth.json`, answer key or training target is required for Stage 6.

## Pipeline map

| Stage | Question |
|-------|----------|
| **5B** | Which scenario owns each ledger row? |
| **5C** | What type is it, and is it authoritative for a fact domain? |
| **5D** | What covenant definition/selectors/modifiers apply? |
| **5E** | What trusted structured facts exist in authoritative sources? |
| **5F** | What calculation-ready transaction/adjustment inputs feed Stage 6? |
| **6** | What is the deterministic covenant actual / activation / compliance result? |
| **7** (next) | How are Stages 3–6 wired into the real competition solver and submission contract? |
| **8** | How does the end-to-end solver behave under adversarial/private-like mutations? |
| **9** | Can a fresh environment reproduce the final submission deterministically? |

## Next

Implement Stage 7 real solver integration. Do not map `NOT_ACTIVATED` into the
competition's binary submission status until that mapping is grounded in the
case specification. Do not infer `evidence_txn_id` from largest / latest / closest
transactions; it must be source- and decision-causal.
