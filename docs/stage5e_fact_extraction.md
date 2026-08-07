# Stage 5E — Structured Fact Extraction

## Architecture

Stage 5E turns Stage 5D covenant semantics into **demand-driven fact requirements**,
then extracts typed facts from **authoritative winning documents only**.

```
CovenantDefinitions + AuthorityDecisions
        │
        ▼
 derive_fact_requirements()   (modifiers / selectors → FactKind)
        │
        ▼
 Deterministic extractors on authoritative docs
        │
        ├─ validate evidence (exact page spans)
        ├─ semantic validate (ownership %, FX>0, txn∈ledger, …)
        └─ conflicts + content-addressed dedupe
        │
        ▼  (optional)
 Model gateway  [--allow-network-models]
```

**Defaults:** deterministic-only, `allow_network=False`, fail-closed (unresolved).
No ledger mutation. No covenant actuals (Stage 5F). No `ground_truth.json` reads.

## Packages

| Package | Role |
|---------|------|
| `domain/fact_extraction` | requirements, extractors, validators, conflicts, engine |
| `domain/models_gateway` | StructuredModelGateway, cache, xAI / Anthropic / mock providers |
| `adapters/facts` | JSONL / manifest / summary I/O |
| `app/facts` | `facts_from_paths` + CLI wiring |

## CLI

```bash
# Deterministic (default)
uv run halyk-agent facts extract \
  --authority work/smoke5c1/authority \
  --covenants work/smoke5d/covenants-polarity \
  --parsed work/smoke541/ocr-enriched \
  --output work/smoke5e/facts \
  --overwrite

# Opt-in model assist (requires provider keys)
uv run halyk-agent facts extract ... --allow-network-models

# Probe configured providers (never HTTP)
uv run halyk-agent models probe
```

## Organizer / env keys

| Variable | Purpose |
|----------|---------|
| `HALYK_LLM_PRIMARY_PROVIDER` | default `xai` |
| `HALYK_LLM_PRIMARY_MODEL` | default `grok-4.5` |
| `HALYK_LLM_ESCALATION_PROVIDER` | default `anthropic` |
| `HALYK_LLM_ESCALATION_MODEL` | default `claude-opus-5` |
| `HALYK_LLM_MAX_CALLS` | call budget |
| `HALYK_LLM_MAX_CONCURRENCY` | concurrency cap |
| `HALYK_LLM_TEMPERATURE` | default `0` |
| `XAI_API_KEY` | xAI / Grok (not `HALYK_`-prefixed) |
| `ANTHROPIC_API_KEY` | optional escalation |

## Outputs

- `fact_requirements.jsonl`
- `fact_candidates.jsonl`
- `accepted_facts.jsonl` / `rejected_facts.jsonl`
- `fact_conflicts.jsonl`
- `model_calls.jsonl` (no secrets)
- `fact_evidence.jsonl`
- `fact_extraction_manifest.json`
- `fact_extraction_summary.md`
