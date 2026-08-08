# Stage 10 — Competition Certification

Status: **COMPLETE**

Certified merged runtime: `main` @ `bae143f5807463572037f0400ae9c3a62fe5b093`
Stage 10 implementation head merged by PR #4: `8780bd2baa5f79dbcfc6ff97888c5db211a46cda`

This document records the final pre-red-team engineering evidence. It is not a claim that the public corpus predicts private-set accuracy. It certifies deterministic execution, leakage boundaries, submission completeness and the behavior of explicitly bounded fallback paths on the supplied public data.

## 1. GitHub CI certification

Final PR #4 validation:

- workflow run: `31267989041`
- job: `93129139036`
- runner: Ubuntu 24.04
- Python: 3.12.13
- environment: `uv sync --frozen --extra full --extra retrieval-full`

Results:

- `uv run ruff format --check .` — pass (`387 files already formatted`)
- `uv run ruff check .` — pass
- `uv run mypy src` — **252 source files, 0 issues**
- `uv run pytest -q` — **794 passed, 22 skipped, 0 failed**

The remaining warnings are dependency deprecations in Starlette/httpx and Docling; neither is a failing correctness gate.

## 2. Certification defects found and closed

### 2.1 Docling model-cache dependence

The real tiny-PDF Docling smoke test previously failed on a clean GitHub runner when Docling attempted to resolve an optional model artifact unavailable in the local cache and raised `LocalEntryNotFoundError` internally.

The test now skips only when:

- the optional Docling full dependency is absent; or
- conversion produced no pages and the typed parser warning identifies `LocalEntryNotFoundError`.

All other parser failures still fail the test. This removes network/cache availability from the correctness gate without hiding actual mapping/parser regressions.

### 2.2 Runtime entropy in Stage 9 lineage

Independent runs originally produced different stable manifest identities because semantic lineage included operational fields such as:

- parse attempt durations;
- OCR timings;
- OCR cache byte counts;
- executable/materialization paths;
- absolute ledger workspace paths.

`semantic_parsed_input_identity` now records semantic/configuration facts only. Ledger provenance stores a stable source basename instead of the temporary workspace path. Regression tests construct equivalent runs with different timings, cache counts and POSIX/Windows paths and require identical identities.

### 2.3 PDFium native thread safety

A repeated full run exposed a native `SIGSEGV` while multiple OCR tasks rendered PDF pages concurrently through the PDFium wrapper.

Stage 10 serializes only the PDFium render call with a process-local lock. External Tesseract subprocess execution remains parallel. A regression test verifies that concurrent OCR requests never execute more than one active PDFium render at once.

## 3. Independent public-corpus reproduction

The clean public archive was extracted into a certification input tree with answer-key/ground-truth files physically excluded. Two full solver runs used independent work/output directories.

Run A:

- run id: `8e14d49fc9cd4e98a152d76a49dff7e4`

Run B:

- run id: `e556ab7ae6ce4a52a67a183ceb85f6eb`

`halyk-agent reproduce-compare` reported:

- deterministic: `true`
- submission SHA-256: `66bfa70e8458ed95a17bd9c194d86693e4c4edf4566d3c66ff84f7e6494f5569`
- fallback diagnostics SHA-256: `c3e71b0849db968a15d196afe8964ee7a35ebfb3047625656b862645aa47eb20`
- evaluation manifest SHA-256: `a8242b91d4cbf29a080f047f8e092ea67f5e7889596cd4f0bed2b31b9e382413`
- source reads: `204 / 204`
- ground-truth access: `none / none`

`submission.json` and `fallback_cells.jsonl` were byte-identical between the two runs. Stable pipeline-manifest fields were identical after excluding the intentional per-run `run_id`.

### Runtime caveat

The final duplicate solve was executed inside the available certification container runtime rather than a newly bootstrapped Python 3.12 virtual environment, because that container could not download a standalone Python interpreter without network access. This does not replace the Python 3.12 compatibility claim: the exact merged source independently passed the full GitHub CI gate under Python 3.12.13.

## 4. Public-corpus pipeline footprint

### Parsing / OCR

- parsed successful: 196
- parsed partial: 3
- parsed failed: 1
- OCR selected pages: 7
- OCR attempted pages: 7
- OCR succeeded pages: 7
- remaining blocking OCR pages: 0

### Routing / authority

- routing resolved documents: 192
- routing unresolved documents: 8
- routing conflicts: 0
- authority classified documents: 163
- authority unknown documents: 37
- authority conflicts: 0

### Covenant / fact / transaction preparation

- covenant definitions: 36
- supported definitions: 36
- covenant compile failures: 0
- accepted facts: 66
- fact-extraction model calls: 0
- calculation inputs: 676
- definition readiness: 35 READY / 1 UNRESOLVED

### Strict Stage 6 evaluation

- results: 36
- resolved: 29
- unresolved: 7
- errors: 0

Strict Stage 6 remains source-faithful and does not silently invent FX, ownership or missing group CAPEX.

## 5. Stage 8 competitive fallback footprint

Exactly seven competition cells required the bounded fallback layer:

- B1 / 6.1
- P1 / 6.1
- P2 / 6.1
- P3 / 6.1
- P5 / 6.1
- P6 / 6.1
- P7 / 6.1

Two strategy records explain the bounded mechanisms:

1. unique public EUR/USD settlement ratio: `1.16`;
2. P5 Note 7 PPE roll-forward residual: `21,847,362.55 USD`.

These values are competition-layer fallbacks, not strict Stage 6 source-authoritative outputs. `fallback_cells.jsonl` remains the audit boundary between the strict evaluator and competitive completion logic.

## 6. Final submission structural checks

The certified public run produced:

- scenarios: 12
- covenant cells: 36 / 36 populated
- COMPLIANT: 21
- BREACH: 15
- final unresolved cells: 0
- null serialized `actual`: 0
- negative serialized `actual`: 0

Exactly one final cell contains a transaction-level evidence id:

- B4 / 6.1 -> `TXN-B4-0026`

That transaction id exists in the supplied ledger. Other cells do not receive a guessed largest/latest/closest transaction.

## 7. Leakage guarantees

The certification run used a dataset tree in which `ground_truth.json` was physically absent. Both pipeline manifests report `ground_truth_access=none`. The reproduction verifier treats any other value as a hard failure.

Production dataset reads remain constrained to the sanitized manifest allowlist. Cross-platform path normalization prevents Windows/POSIX aliases from bypassing quarantine membership.

## 8. Submission-time checklist

Before generating the actual contest artifact:

1. use the merged `main` runtime unless a concrete red-team fix is accepted;
2. inject the actual `team`, `contact_email`, and `model` metadata expected by the submission template/operator;
3. run submission schema/shape validation once after metadata injection;
4. confirm 12 scenarios / 36 cells and no null status/actual fields;
5. confirm `ground_truth_access=none` in the run manifest;
6. archive `submission.json`, `pipeline_manifest.json`, `fallback_cells.jsonl`, and the exact Git SHA together;
7. do not change calculation/fallback semantics after final certification without rerunning CI and independent reproduction.

## 9. Next gate

The next useful activity is external adversarial review, not another implementation stage. Claude Opus 5 should review the merged `main` repository with special attention to:

- Stage 8 fallback generalization risk on private data;
- source-authority / period / currency edge cases;
- causal evidence semantics;
- leakage boundaries;
- submission contract interpretation;
- failure behavior under malformed or shifted private inputs.

Only concrete findings should trigger code changes. Any accepted runtime change invalidates this certification snapshot until CI and independent-run reproduction are rerun.
