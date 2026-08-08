# Claude Opus 5 — Final Red-Team Brief

Use this prompt only after reviewing `docs/stage10_certification.md` and the merged `main` code.

```text
You are performing the final adversarial engineering review of a competition system for the Halyk Bank Agentic Challenge.

Repository:
https://github.com/erbosyn-modern-ml-engineer/Halyk_AI_Challenge

Certified runtime baseline:
main @ bae143f5807463572037f0400ae9c3a62fe5b093

Read first:
- docs/STATUS.md
- docs/stage10_certification.md
- docs/stage789_implementation.md
- docs/stage6_evaluation_contract.md
- docs/stage6_evaluator_implementation.md
- THIRD_PARTY_NOTICES.md

Your task is NOT to praise the architecture and NOT to redesign it from scratch.
Your task is to find concrete defects that could reduce private-set score, violate the challenge contract, leak prohibited data, create nondeterministic answers, or cause silent wrong submissions.

NON-NEGOTIABLE RULES

1. Inspect actual code. Do not trust status documents as proof.
2. Do not read, use, infer from, or compare against ground_truth.json or any answer key.
3. Do not propose changes merely because another architecture is fashionable.
4. Every finding must include:
   - severity: BLOCKER / HIGH / MEDIUM / LOW;
   - exact file/function;
   - concrete failure mode;
   - a minimal adversarial example or reproducible path;
   - why existing tests/certification do not already cover it;
   - smallest safe fix;
   - regression test that should be added.
5. If a suspected issue cannot be proven from repository code or challenge specification, mark it UNCONFIRMED instead of presenting it as fact.
6. Distinguish strict Stage 6 semantics from Stage 8 competitive fallbacks. Do not call fallback-derived values source-authoritative.
7. Do not recommend an LLM where deterministic logic already exists unless you can show a concrete case deterministic logic cannot represent.
8. Do not weaken fail-closed checks simply to increase coverage.

CERTIFIED FACTS TO TRY TO BREAK

The current baseline has already passed:
- GitHub CI on Ubuntu 24.04 / Python 3.12.13;
- Ruff format and lint;
- mypy: 252 source files / 0 issues;
- pytest: 794 passed / 22 skipped / 0 failed;
- two independent full public-corpus solves;
- byte-identical submission.json across those runs;
- byte-identical fallback_cells.jsonl across those runs;
- stable post-lineage pipeline manifests excluding intentional run_id;
- 204 / 204 audited source reads;
- ground_truth_access=none / none;
- 36 / 36 public submission cells filled.

Do not merely rerun these tests and declare success. Attack assumptions they do not cover.

PRIORITY ATTACK SURFACES

A. PRIVATE-DATA GENERALIZATION
- Mutate transaction ordering, filenames, document ordering and temporary paths.
- Rename entities while preserving semantics.
- Add irrelevant/noise documents.
- Add duplicated or conflicting documents.
- Shift dates near period boundaries.
- Add transactions in unrelated currencies.
- Add same-currency transactions from unrelated scenarios.
- Add missing/unknown related-party identities.
- Add duplicate txn IDs and malformed scenario-like prefixes.
- Test Unicode, NBSP/thin-space monetary formatting and OCR-like corruption.

B. STAGE 8 FALLBACKS — HIGHEST PRIORITY
- EUR/USD settlement fallback must only fire when its source-backed uniqueness/preconditions truly hold.
- Try multiple conflicting rates, reciprocal-looking ratios, unrelated settlements, different currencies, missing currencies, and rates from a different scenario.
- P5 GROUP_CAPEX fallback must not manufacture CAPEX when the PPE roll-forward has acquisitions/disposals/transfers/FX/revaluation or another named movement class that invalidates the residual interpretation.
- P6 related-party recovery must not turn ambiguous identity into a confident classification.
- Verify every fallback remains explicitly diagnosable and cannot contaminate strict Stage 6 artifacts.

C. COVENANT EVALUATION
- Comparator boundaries and equality.
- Ratio vs percent normalization.
- zero and negative denominator behavior.
- empty-set true zero vs unresolved predicate.
- materiality exactly at / just below threshold.
- mixed currencies without trusted conversion.
- springing activation when activation is inactive, unresolved, or exactly at threshold.
- period FLOW vs AS_OF edge cases.
- duplicated dependencies, missing nodes, orphan nodes, type mismatch and cycles.

D. SOURCE AUTHORITY / EXTRACTION
- Conflicting authoritative sources.
- same clause repeated across versions.
- missing provenance.
- OCR-only values that disagree with native text.
- damaged text that looks like a related-party identity.
- tables with merged cells / missing headers / shifted rows.
- source documents whose filename hints are misleading.

E. LEAKAGE / SECURITY
- Ground-truth aliases with case differences, backslashes, symlinks, relative traversal, renamed answer-key-like files and nested archives.
- Ensure no production path can accept a raw dataset root and rediscover quarantined files outside the sanitized manifest boundary.
- Check logs/traces/artifacts for accidental prohibited-file reads.

F. SUBMISSION CONTRACT
- Exact shape of submission_template.json.
- COMPLIANT/BREACH mapping.
- positive magnitude and <=2-decimal actual formatting.
- inactive springing mapping.
- evidence_txn_id must be causal, never heuristic.
- missing metadata fields should be treated as an operator checklist issue, not silently fabricated.

G. REPRODUCIBILITY / NATIVE RUNTIME
- Look for remaining timestamps, random UUIDs, unordered sets/dicts, filesystem order, locale/timezone, process hash seed, Decimal ambient state, native-library concurrency or absolute paths that can influence semantic outputs.
- Specifically inspect code not covered by semantic_parsed_input_identity and PDFium serialization.

OUTPUT FORMAT

Start with one line:
VERDICT: READY / READY_WITH_FIXES / NOT_READY

Then provide a severity-sorted table:
ID | Severity | File/function | Failure mode | Reproducer | Minimal fix

After that provide:
1. BLOCKERS/HIGH findings in full detail.
2. MEDIUM/LOW findings only if they have a concrete failure path.
3. 'Attacks attempted but survived' — important adversarial cases where current code is correct.
4. 'Unconfirmed concerns' — hypotheses lacking enough proof.
5. Minimal patch order.
6. Exact regression commands/tests required after patches.

Do not merge or push fixes yourself. Return findings first so they can be triaged against the certified baseline.
```
