# Stage 10.5 public validation

Training-only public scoring was performed strictly after the production solver produced `submission.json`; ground truth was not available to the production pipeline.

Results on the public corpus after Stage 10.4 + Stage 10.5 evidence replay:

- cells: 36;
- `actual`: 36/36 exact;
- expected non-null `evidence_txn_id`: 9;
- exact non-null evidence IDs: 9/9;
- status: 35/36 exact;
- uniform total: 35.00 / 36.00;
- uniform mean: 97.22%.

The remaining public error is isolated to P4/6.3 status semantics. This document intentionally does not encode any ground-truth answer value into production logic.
