# Status

Current engineering state: **Stages 6–10 implemented and Stage 10 certification completed.**

Current production branch: `main`
Certified Stage 10 merge: `bae143f5807463572037f0400ae9c3a62fe5b093`

Base: final Stage 6 pre-flight closure @ `86bb9908b789a9026c64a5d2a09d95b431d242ee`.

## Stage 6 — deterministic covenant evaluator

Implemented:

- typed `CovenantDefinition -> EvaluationPlan` planning;
- strict DAG / type / modifier validation;
- Stage 5F context validation and artifact-hash binding;
- FLOW / AS_OF period semantics;
- materiality-floor evaluation nodes;
- local Decimal context (`prec=60`, `ROUND_HALF_EVEN`), no float;
- no implicit FX;
- deterministic calculation trace;
- springing activation while still computing the main metric required by the competition contract.

Stage 6 remains strict/source-faithful. It does not use Stage 8 competitive fallbacks.

## Stage 7 — real competition solver integration

Implemented production path:

`sanitized manifest -> audited source copies -> parallel FAST parse -> selective OCR -> routing -> authority -> covenant compile -> fact extraction -> Stage 5F -> Stage 6 -> exact submission adapter`

Competition-contract closures from `CASE.ru.md`:

- submission status is exactly `COMPLIANT` / `BREACH`;
- `actual` is always the main covenant metric, including inactive springing covenants;
- inactive springing covenant maps to submission `COMPLIANT` only after the main actual is computed;
- `evidence_txn_id` is causal only; largest/latest/threshold-crossing heuristics are forbidden;
- final submission `actual` is positive magnitude and rounded only in the submission layer.

The solver reads only sanitized allowlisted paths. `ground_truth.json` is neither required nor read.

## Stage 8 — bounded competitive fallbacks

Strict Stage 6 output is preserved separately. Stage 8 is an explicitly labeled competition layer used only for strict non-resolved cells because a blank scores the same as an incorrect answer.

Certified public-corpus strict evaluation:

- 36 covenant cells;
- strict Stage 6: **29 resolved / 7 unresolved / 0 errors**;
- Stage 5F: **35 READY / 1 UNRESOLVED** after narrow damaged-related-party recovery;
- source reads: **204**;
- fact extraction model calls: **0**.

Bounded fallbacks:

1. **EUR/USD settlement-rate fallback** — derives the unique source-backed settlement ratio when exactly one non-conflicting ratio exists. On the supplied public corpus this ratio is `1.16`. Strict Stage 6 still refuses cross-scenario FX generalization; only the competitive fallback uses it.
2. **P5 GROUP_CAPEX fallback** — reconstructs the Note 7 PPE roll-forward residual only when opening NBV, depreciation, closing NBV and zero disposals are source-backed and no competing movement class is named. Public residual: `21,847,362.55 USD`.

The competitive public run fills all **36 / 36** template cells. Fallback outputs remain separately diagnosable in `fallback_cells.jsonl` and must not be described as strict/source-authoritative Stage 6 results.

## Stage 9 — independent-run reproduction

Completed after the final lineage-normalization fixes.

Two independent full public-corpus solves were run in separate work/output directories from a clean extracted dataset in which `ground_truth.json` was physically absent. `halyk-agent reproduce-compare` reported:

- deterministic: **true**;
- submission SHA-256: `66bfa70e8458ed95a17bd9c194d86693e4c4edf4566d3c66ff84f7e6494f5569`;
- fallback diagnostics SHA-256: `c3e71b0849db968a15d196afe8964ee7a35ebfb3047625656b862645aa47eb20`;
- evaluation manifest SHA-256: `a8242b91d4cbf29a080f047f8e092ea67f5e7889596cd4f0bed2b31b9e382413`;
- source reads: **204 / 204**;
- ground-truth access: **none / none**.

Runtime-path, parse-duration, OCR-cache/path and ledger-workspace entropy are excluded from semantic lineage. PDFium rendering is serialized while Tesseract subprocess concurrency is retained.

## Stage 10 — competition certification

Stage 10 closed three certification defects discovered during independent reproduction:

1. **Docling CI cache dependence** — the real tiny-PDF smoke test now distinguishes a missing optional model/cache artifact (`LocalEntryNotFoundError`) from an actual parser regression. Only the former is skipped.
2. **Runtime entropy in lineage** — routing/authority identities now use semantic parser/OCR configuration and stable source identity rather than timings, cache byte counts or workspace paths.
3. **Concurrent PDFium rendering** — PDFium wrapper calls are serialized to prevent non-deterministic native crashes; OCR subprocess work remains concurrent.

### Final GitHub CI gate

PR #4 / Stage 10 validation on Ubuntu 24.04, Python 3.12.13:

- `ruff format --check .` — pass (`387 files already formatted`);
- `ruff check .` — pass;
- `mypy src` — **252 source files, 0 issues**;
- `pytest -q` — **794 passed, 22 skipped, 0 failed**.

The CI environment installs both `full` and `retrieval-full` extras.

### Fresh public-run footprint

The certified run produced:

- 12 scenarios / 36 covenant cells;
- submission: **21 COMPLIANT / 15 BREACH**;
- no null or negative serialized `actual` values;
- exactly one non-null causal `evidence_txn_id`: `B4 / 6.1 -> TXN-B4-0026`, present in the ledger;
- OCR: 7 selected / 7 attempted / 7 succeeded / 0 blocking pages remaining;
- routing: 192 resolved documents / 8 unresolved / 0 conflicts;
- authority: 163 classified / 37 unknown / 0 conflicts;
- covenant compile: 36 definitions / 36 supported / 0 failures;
- facts: 66 accepted / 0 model calls;
- calculation inputs: 676;
- fallback cells: 7;
- final unresolved competition cells: **0**.

The full reproduction run executed in the certification container with its available Python runtime, while the supported Python 3.12 environment is independently validated by the green GitHub CI gate above. This distinction is intentional and should not be conflated.

See `docs/stage10_certification.md` for the certification evidence and remaining submission-time checklist.

## Remaining work before competition submission

No additional numbered implementation stage is justified before external red-team review.

1. Run Claude Opus 5 as a final adversarial reviewer against the merged `main` code and this certification record.
2. Triage only concrete findings; do not reopen already-proven stages without evidence.
3. Before producing the actual contest file, pass real metadata values for `team`, `contact_email`, and `model` if required by the submission template/operator.
4. Perform one final submission-only validation after metadata injection; do not alter calculation logic unless a red-team finding demonstrates a defect.
