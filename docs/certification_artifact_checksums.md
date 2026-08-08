# Stage 10 certification artifact checksums

These values bind the final independent public-corpus reproduction described in `docs/stage10_certification.md`.

| Artifact | SHA-256 |
|---|---|
| `submission.json` | `66bfa70e8458ed95a17bd9c194d86693e4c4edf4566d3c66ff84f7e6494f5569` |
| `fallback_cells.jsonl` | `c3e71b0849db968a15d196afe8964ee7a35ebfb3047625656b862645aa47eb20` |
| Stage 6 evaluation manifest | `a8242b91d4cbf29a080f047f8e092ea67f5e7889596cd4f0bed2b31b9e382413` |

Reproduction run IDs:

- A: `8e14d49fc9cd4e98a152d76a49dff7e4`
- B: `e556ab7ae6ce4a52a67a183ceb85f6eb`

Audited source reads:

- A: `204`
- B: `204`

Ground-truth access:

- A: `none`
- B: `none`

The per-run `run_id` is intentionally excluded from stable pipeline-manifest equality. All other stable fields compared by `halyk-agent reproduce-compare` matched.
