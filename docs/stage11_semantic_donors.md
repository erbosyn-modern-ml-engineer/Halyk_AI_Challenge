# Stage 11 semantic fallback donor review

Stage 11 uses external repositories as design donors only. No donor is vendored and no donor runtime dependency is introduced by this hardening pass.

## Adopted patterns

### google/langextract — Apache-2.0

Pinned review revision: `b5fe0baf807ac35ec95b968a71e4d03f198a1b60`.

Useful pattern: model output is not trusted as source coordinates; extracted text is aligned back to the source by deterministic code. Halyk adopts the stricter subset: **exact contiguous source quote only**. Fuzzy alignment is intentionally not used for authority, covenant, or fact evidence.

Applied to:

- covenant semantic AST proposals;
- document type/lifecycle semantic proposals;
- existing Stage 5E fact validation.

### sebastienrousseau/bankstatementparser — Apache-2.0

Pinned review revision: `f3b159aed8bfe97d06f77ca6c95633593c448466`.

Useful pattern: cheapest deterministic path first, LLM/vision only as a fallback, explicit `source_method`, then deterministic verification. Halyk adopts the orchestration principle rather than its bank-statement schema.

Applied to:

- deterministic transaction classification before semantic classification;
- semantic fallback only for `UNRESOLVED` transaction descriptions;
- semantic document fallback only for unresolved type/lifecycle states;
- deterministic Stage 5F/Stage 6 verification after any accepted proposal.

### Future-House/paper-qa — Apache-2.0

Existing project donor. Useful pattern: evidence/citation is first-class output rather than narrative decoration. Halyk keeps evidence as typed source spans and causal transaction replay rather than free-form citations.

### JerBouma/FinanceToolkit — MIT

Existing Stage 10.4 donor. Useful pattern: ratios consume explicit financial components. Halyk keeps covenant arithmetic deterministic and does not let an LLM broaden financial categories or compute the final ratio.

## Reviewed but not imported

- `pydantic/pydantic-ai` and Instructor: strong structured-output/retry patterns, but adding another agent/model framework would duplicate the existing typed gateway and expand the dependency surface.
- `deepset-ai/haystack`: useful conditional routing concepts, but too broad for a competition runtime whose semantic fallback is only a few bounded tasks.
- legal RAG repositories: useful evidence discipline, but they do not replace the project's deterministic authority, accounting, and causal-evidence contracts.

## Stage 11 invariant

The LLM can propose semantics. It cannot publish financial truth.

Accepted proposals must still pass enum/Pydantic validation, exact source grounding, scenario ownership, conflict/readiness checks, deterministic Stage 5F normalization, deterministic Stage 6 arithmetic, and causal-evidence replay where applicable.
