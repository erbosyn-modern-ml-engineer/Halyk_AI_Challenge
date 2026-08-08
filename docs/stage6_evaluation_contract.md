# Stage 6 — Evaluation Contract (Pre-flight Freeze)

Status: **CONTRACT CLOSED (pre-flight)** — evaluator not implemented.

This document freezes the Stage 6 input / validation contracts after Opus findings
BLOCKER-1/2 and HIGH-1..4. It does **not** authorize EvaluationPlanner /
EvaluationExecutor / evaluate CLI implementation.

## Upstream readiness (source-faithful)

| Signal | Expectation with current public source data |
|--------|---------------------------------------------|
| Stage 5D definitions | 36 |
| Stage 5F structural READY / UNRESOLVED | 34 / 2 |
| Structural UNRESOLVED | P5 `GROUP_CAPEX`, P6 related-party identity |
| Stage 6 numeric evaluability (current sources) | **29** |
| Currency fail-closed | five otherwise-READY definitions with mixed USD/EUR operands and no trusted conversion |

Recompute the mixed-currency set from regenerated Stage 5F artifacts; do not trust a
frozen scenario list if upstream inputs change. With the Stage 5F.3 corpus the five are:

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

## Typed Stage 6 inputs (closed by pre-flight)

1. **MATERIALITY_FLOOR** carries `threshold: TypedQuantity` and optional
   `applies_to_category: MetricCategory`. Missing/ambiguous floor fails closed at
   compile time (no parameter-less modifier).
2. Stage 5F manifest hashes **selector_coverage** and **definition_readiness**
   (`selector_coverage_hash`, `definition_readiness_hash`). Consumers must verify
   via `verify_taxonomy_readiness_hashes` before evaluation publication.
3. Every evaluation-bound `CalculationInput` carries `InputPeriodSemantics`:
   - ledger flows → `FLOW`
   - P4 one-time add-backs → `FLOW` (requirement/covenant flow binding)
   - P8 severance liability → `AS_OF` with source-backed `as_of_date`
4. Period helpers remain in `domain/transaction_taxonomy/period.py`
   (tri-state: `None` means undecidable, not false).

## Materiality money safety (closed by materiality-safety-fix)

Parsing rules for `MATERIALITY_FLOOR.threshold` (and other typed money thresholds
that share the covenant money scanner):

- **Full-token validation.** A currency-prefixed candidate is accepted only when
  the entire monetary numeric token is syntactically valid. A valid numeric
  prefix followed by letter/junk continuation is malformed.
- **No OCR auto-correction.** Letter-as-digit corruption must not be rewritten
  into digits; it fails closed (no published threshold).
- **Bounded candidate collection.** Materiality extraction scans the relevant
  instruction/sentence region only (not document-wide).
- **Distinct-candidate ambiguity.** After typed-value dedupe
  `(currency, Decimal value)`:
  - 0 valid → no materiality threshold
  - 1 valid → publish `MATERIALITY_FLOOR`
  - >1 distinct → ambiguous / no confident modifier
- Identical repeats of the same typed amount may dedupe. Unrelated money outside
  the bounded instruction must not create false ambiguity.

## Validation split (HIGH-4)

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
- source-quality compatibility

Both fail closed. **No output publication** on global validation failure.

## Frozen implementation-time rules (executor not built yet)

### 1. POST-FILTER EMPTY

`SELECT READY` with inputs → deterministic decidable filter → zero surviving inputs
is a genuine zero for `SUM` (example: materiality floor filters every add-back).

But if the filter predicate is undecidable for any relevant input → `UNRESOLVED`.
Never silently drop undecidable rows.

### 2. DECIMAL POLICY

Stage 6 must run under an explicit local `Decimal` Context. Do not depend on ambient
process context. Use deterministic high precision appropriate for financial ratios
(precision ≥ 50) with an explicit rounding policy. Do not quantize covenant actuals
before comparison unless the covenant explicitly specifies rounding. Comparison uses
the fixed context. No float.

### 3. NEGATIVE DENOMINATOR

Do not reinterpret the formula. If the compiled denominator is negative, evaluate
faithfully and emit deterministic diagnostic `NEGATIVE_DENOMINATOR` in node result /
evaluation issues. Zero denominator remains `ERROR`.

## Future tests location

Do **not** place covenant evaluator tests in `tests/evaluation/` (retrieval metrics).

Freeze:

`tests/covenant_evaluation/`

(Not created in this pre-flight unless architecture-contract tests require it.)

## Non-goals

- No EvaluationPlanner / EvaluationPlan / EvaluationExecutor
- No covenant actuals / statuses / evaluate CLI
- No Stage 7
- No push to 36/36 by inventing FX, OCR identity, or GROUP_CAPEX
