# Model pins (Stage 4)

Immutable Hugging Face revisions for Stage 4 retrieval. Do not advance to rolling
`main` without an explicit architecture decision. Machine-readable copy:
[`model-lock.json`](../model-lock.json).

Offline operation after prewarm / cache:

```text
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
```

| logical_name | repository_or_model_id | revision | license | dimension | max_input_tokens | query_prefix | passage_prefix | purpose | status |
|--------------|------------------------|----------|---------|-----------|------------------|--------------|----------------|---------|--------|
| full-embedding | intfloat/multilingual-e5-small | `614241f622f53c4eeff9890bdc4f31cfecc418b3` | MIT | 384 | 512 | `query: ` | `passage: ` | Authoritative competition (FULL) dense embeddings | default |
| fast-embedding | intfloat/multilingual-e5-small | `614241f622f53c4eeff9890bdc4f31cfecc418b3` | MIT | 384 | 512 | `query: ` | `passage: ` | Legacy FAST alias (same weights) | legacy_fast_alias |
| optional-bge-m3 | BAAI/bge-m3 | `5617a9f61b028005a4858fdac845db406aefb181` | MIT | 1024 | 8192 | *(empty)* | *(empty)* | Optional large dense embeddings | optional_large_model / requires_explicit_user_approval / not_preverified |
| full-reranker | BAAI/bge-reranker-v2-m3 | `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e` | Apache-2.0 | — | — | *(empty)* | *(empty)* | Optional cross-encoder (disabled by default) | optional_large_model / requires_explicit_user_approval / not_preverified |

## Download policy

- Max automatic single artifact: **500 MB**
- Max Stage 4.2 total automatic download: **800 MB**
- BGE-M3 (~2.2 GB) and BGE reranker (~1.1 GB) are **blocked** unless `HALYK_ALLOW_LARGE_MODEL_DOWNLOAD=1` or CLI `--approve-large-models`
- Default `models prewarm --profile full --components embeddings` loads **E5-small only**

## Notes

* E5 requires the documented query/passage prefixes exactly as shown.
* BGE-M3 is not the competition default and must not auto-download.
* Reranker scores must not erase original lexical, vector, or RRF ranks/scores when optionally enabled.
* No model may silently download during query execution when offline mode is enabled.
