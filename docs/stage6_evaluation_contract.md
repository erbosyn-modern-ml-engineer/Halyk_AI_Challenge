# Stage 6 — Evaluation Contract

Status: **IMPLEMENTED** on `stage-6/covenant-evaluator`.

This contract was frozen before implementation after Opus findings BLOCKER-1/2 and
HIGH-1..4. The Stage 6 branch now implements the planner, structural/context
validators, Decimal executor, deterministic trace and replay CLI described here.
The source-faithful public-corpus counts below remain expectations from the last
known Stage 5F.3 artifacts until those gitignored artifacts are regenerated.

## Upstream readiness (source-faithful)

| Signal | Expectation with current public source data |
|--------|---------------------------------------------|
| Stage 5D definitions | 36 |
| Stage 5F structural READY / UNRESOLVED | 34 / 2 |
| Structural UNRESOLVED | P5 `GROUP_CAPEX`, P6 related-party identity |
| Stage 6 numeric evaluability (current sources) | **29** |
| Currency fail-closed | five otherwise-READY definitions with mixed USD/EUR operands and no trusted conversion |

Recompute the mixed-currency set from regenerated Stage 5F artifacts; do not trust a
frozen scenario list if upstream inputs change. With the Stage 5F.3 corpus the five were:

- B1 6.1
- P1 6.1
- P2 6.1
- P3 6.1
- P7 6.1

Diagnostic code for those cases:

`MIXED_CURRENCY_NO_TRUSTED_CONVERSION`

Rules:

- Structural READY ≠ numeric READY.
- The single P3 FX settlement fact (`explicit_rate=None`, `rate_source=NOT_STATED`,
  `transaction_id=None`) must **not** be applied to unrelated EUR ledger rows.
- No invented FX, no reciprocal guessing, no settlement/source-derived rates.

## Typed Stage 6 inputs

1. **MATERIALITY_FLOOR** carries `threshold: TypedQuantity` and optional
   `applies_to_category: MetricCategory`. Missing/ambiguous floor fails closed at
   compile time (no parameter-less modifier).
2. Stage 5F manifest hashes **selector_coverage** and **definition_readiness**
   (`selector_coverage_hash`, `definition_readiness_hash`). Stage 6 verifies them
   with `verify_taxonomy_readiness_hashes` before evaluation publication.
3. Every evaluation-bound `CalculationInput` carries `InputPeriodSemantics`:
   - ledger flows → `FLOW`
   - P4 one-time add-backs → `FLOW` (requirement/covenant flow binding)
   - P8 severance liability → `AS_OF` with source-backed `as_of_date`
4. Period helpers remain in `domain/transaction_taxonomy/period.py`
   (tri-state: `None` means undecidable, not false).

## Materiality money safety

Parsing rules for `MATERIALITY_FLOOR.threshold` (and other typed money thresholds
that share the covenant money scanner):

- **Complete monetary token grammar.** One lexer recognizes plain digits,
  comma thousands, and space thousands (incl. NBSP / narrow NBSP / thin space),
  plus an optional `.` decimal part. Grouping must be well-formed
  (1–3 / 3 / 3…). Hyphen/apostrophe mixtures and OCR letter-as-digit tokens
  fail closed — never emit a shorter valid prefix.
- **No OCR auto-correction.** Letter-as-digit corruption is not rewritten into digits.
- **Instruction region invariant.** A materiality/add-back regex match always
  contributes a region with `end >= match.end()`, then expands to the sentence
  containing `match.end() - 1`. Legal abbreviations (`п.`, `ст.`, `cl.`, `Sec.`)
  are not sentence terminators.
- **All-instruction reconciliation.** Every matched materiality / add-back-floor
  instruction is scanned; document-level candidates are reconciled:
  - any relevant malformed threshold-like money → no confident modifier
  - 0 distinct typed floors → no modifier
  - 1 distinct `(currency, Decimal)` → publish
  - >1 distinct → ambiguous / no modifier
  - identical typed repeats dedupe (no first/last/highest preference)
- Unrelated money outside a matched materiality instruction must not create
  false ambiguity. Ranges like `$300,000-$500,000` discover both endpoints.

## Validation split

### A. PlanStructureValidator

Runs after planning, **before** data binding.

Owns:

- duplicate node IDs
- missing dependency
- missing root
- cycle
- unsupported node kind
- node payload validation
- AST / type compatibility
- unsupported modifier shape
- orphan nodes not reachable from either main or activation root

### B. ContextValidator

Runs **after** Stage 5F inputs / selector coverage are bound.

Owns:

- `amount_contract_version`
- selector existence
- selector scenario ownership
- coverage state availability
- input IDs
- scenario universe
- currency conflict
- period consistency
- Stage 5F definition readiness

Both fail closed. **No output publication** on global validation failure.

## Executor rules

### 1. POST-FILTER EMPTY

`SELECT READY` with inputs → deterministic decidable filter → zero surviving inputs
is a genuine zero for `SUM` (example: materiality floor filters every add-back).

But if the filter predicate is undecidable for any relevant input → `UNRESOLVED`.
Never silently drop undecidable rows.

### 2. DECIMAL POLICY

Stage 6 runs under an explicit local `Decimal` Context with precision 60 and
`ROUND_HALF_EVEN`. It does not depend on ambient process context. Covenant actuals
are not quantized before comparison unless the covenant explicitly specifies
rounding. No float.

### 3. NEGATIVE DENOMINATOR

If the compiled denominator is negative, evaluate faithfully and emit deterministic
`NEGATIVE_DENOMINATOR` in the trace/issues. Zero denominator is `ERROR`.

### 4. SPRINGING ACTIVATION

Activation is a separate dependency subgraph. The executor evaluates it first. If
inactive, Stage 6 returns internal `NOT_ACTIVATED` / `activation_state=INACTIVE`
and does not execute unrelated main-metric nodes. The final competition mapping of
that state to the binary submission schema is intentionally deferred to Stage 7
until grounded in the case specification.

## Tests

Evaluator tests live in:

`tests/covenant_evaluation/`

The Stage 6 implementation was validated on GitHub Actions / Ubuntu 24.04 / Python
3.12 with:

- Ruff format: pass
- Ruff lint: pass
- mypy: 245 source files, 0 issues
- pytest: 779 passed, 22 skipped, 0 failed

## Non-goals

- No invented FX / ownership / GROUP_CAPEX to force 36/36 numeric coverage
- No ground-truth or answer-key reads
- No LLM calls inside Stage 6
- No submission-status guessing
- No heuristic `evidence_txn_id` selection
