# Stage 10.4 — Financial-ratio statement semantics

## Purpose

The Stage 10.4 patch narrows covenant transaction membership to the statement
components explicitly named by each covenant. It does not hard-code public
scenario IDs, expected answers, thresholds, or transaction IDs.

The key correction is that a covenant selector named **Operating Expenses** is
not a generic accounting super-category. Specialized lines such as labor,
utilities, insurance, rent, taxes and unrelated consulting/marketing costs stay
in their own typed categories unless source evidence explicitly reclassifies
them into Operating Expenses.

## Donor implementation reviewed

### FinanceToolkit

- Repository: `JerBouma/FinanceToolkit`
- Pinned revision: `7dab0dd68200f0789da72868a63856768a8cd9b7`
- License: MIT
- File reviewed: `financetoolkit/ratios/solvency_model.py`
- Adapted principle: solvency ratios consume explicit, already-defined statement
  components (for example operating income, depreciation/amortization, interest
  expense and net debt). Ratio functions do not widen one component into every
  vaguely related expense family.
- No FinanceToolkit runtime dependency or source code is vendored. Halyk keeps
  its typed Decimal DAG evaluator and applies the statement-component separation
  in its own transaction-taxonomy layer.

`Accord-Project/accord-nlp` was also reviewed as a conceptual rule-extraction
reference, but no parser change or code adaptation from that project is part of
this patch.

## Semantic changes

1. `OPEX` becomes an exact statement-line selector rather than a hierarchy over
   labor, utilities, insurance, rent and taxes.
2. Generic expense-like descriptions are retained as `OTHER_EXPENSE` for audit
   provenance and related-party overlays, but they do not enter OPEX merely
   because they are expenses.
3. Direct industrial operating/servicing descriptions can still be OPEX when
   their wording establishes an operating-cost role.
4. A property/land/warehouse lease can additionally satisfy `RENT`; a telecom
   leased line remains `UTILITIES`.
5. Capital-asset transfers retain `CAPEX` membership for aggregate capital-spend
   denominators while their transfer category controls transfer numerators.
6. Capitalised interest remains an interest financing cost unless authoritative
   source evidence explicitly reclassifies it to CAPEX.
7. An authoritative one-time add-back is modeled expense-first and add-back-second.
   If no unique ledger row is attachable, paired OPEX and add-back derived inputs
   preserve that accounting identity.

## Public validation protocol

Ground truth is never available to the production solver. Public scoring is a
separate `HALYK_MODE=training` evaluation performed only after `submission.json`
has been produced. The patch was selected because the semantic correction is
source-grounded and generalizable; public scoring is used only as an external
validation signal.
