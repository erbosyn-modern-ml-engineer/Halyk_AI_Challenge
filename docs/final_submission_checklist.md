# Final submission operator checklist

Use this after the final red-team review and before uploading the contest JSON.

- [ ] Checkout the accepted certified `main` SHA.
- [ ] Confirm no uncommitted runtime changes.
- [ ] Use only the sanitized/allowlisted dataset path.
- [ ] Confirm `ground_truth.json` / answer-key files are not in the solver input tree.
- [ ] Supply real `team`, `contact_email`, and `model` metadata.
- [ ] Run the competition solve once.
- [ ] Confirm `pipeline_manifest.json` reports `ground_truth_access: none`.
- [ ] Confirm exactly 12 scenarios and 36 covenant cells.
- [ ] Confirm every cell has `status` in `{COMPLIANT, BREACH}`.
- [ ] Confirm every serialized `actual` is non-null, non-negative, and submission-rounded.
- [ ] Confirm any `evidence_txn_id` is causal and exists in the ledger.
- [ ] Keep `fallback_cells.jsonl` beside the submission for audit; do not submit it unless the competition requests it.
- [ ] Record the exact Git SHA and SHA-256 of the final `submission.json`.
- [ ] Do not edit the JSON manually after validation.
