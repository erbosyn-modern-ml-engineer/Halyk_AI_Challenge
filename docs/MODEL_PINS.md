# Model pins (Stage 4)

Immutable Hugging Face revisions for Stage 4 retrieval. Do not advance to rolling
`main` without an explicit architecture decision. Machine-readable copy:
[`model-lock.json`](../model-lock.json).

Offline operation after prewarm:

```text
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
```

| logical_name | repository_or_model_id | revision | license | dimension | max_input_tokens | query_prefix | passage_prefix | purpose |
|--------------|------------------------|----------|---------|-----------|------------------|--------------|----------------|---------|
| fast-embedding | intfloat/multilingual-e5-small | `614241f622f53c4eeff9890bdc4f31cfecc418b3` | MIT | 384 | 512 | `query: ` | `passage: ` | FAST dense embeddings |
| full-embedding | BAAI/bge-m3 | `5617a9f61b028005a4858fdac845db406aefb181` | MIT | 1024 | 8192 | *(empty)* | *(empty)* | FULL dense embeddings (dense only in Stage 4) |
| full-reranker | BAAI/bge-reranker-v2-m3 | `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e` | Apache-2.0 | — | — | *(empty)* | *(empty)* | FULL cross-encoder reranking after hybrid fusion |

## Notes

* E5 requires the documented query/passage prefixes exactly as shown.
* BGE-M3 Stage 4 uses dense vectors only (no sparse / multi-vector retrieval yet).
* Reranker scores must not erase original lexical, vector, or RRF ranks/scores.
* No model may silently download during query execution when offline mode is enabled.
