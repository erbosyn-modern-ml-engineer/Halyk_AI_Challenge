# Stage 5E — Structured Fact Extraction

**Status:** `LIVE_PROVIDER_UNVERIFIED` (DeepSeek wired offline; no live API verification in CI)

## Architecture

Stage 5E turns Stage 5D covenant semantics into **demand-driven fact requirements**,
then extracts typed facts from **authoritative winning documents only**.

```
CovenantDefinitions + AuthorityDecisions (+ winning docs for source triggers)
        │
        ▼
 derive_fact_requirements()   two-phase:
   1) SEMANTIC_REQUIRED from covenants (never gated on authority existing)
   2) SOURCE_TRIGGERED_CONDITIONAL from strong cues in Stage 5C winners
   SPECULATIVE forbidden (count must be 0)
        │
        ▼
 Deterministic extractors on authoritative docs
   ├─ CONFIRMED_NONE for explicit negatives (no fake positive facts)
   ├─ REJECTED disposition for proposed-but-rejected reclass
   ├─ source-faithful FX (no invented rates) + period service dates
   ├─ validate evidence (exact page spans; fragment-bound for LLM)
   └─ conflicts + content-addressed dedupe
        │
        ▼  (optional, gated)
 Model gateway  [--allow-network-models]
   DeepSeek V4 Flash primary (thinking disabled)
   DeepSeek thinking escalation (enabled + reasoning_effort=high)
   max_external_attempts budget counts every HTTP (retries/escalation)
```

**Defaults:** deterministic-only, `allow_network=False`, fail-closed.
No ledger mutation. No covenant actuals (Stage 5F). No `ground_truth.json` reads.

## Packages

| Package | Role |
|---------|------|
| `domain/fact_extraction` | requirements, extractors, validators, terminal states, engine |
| `domain/models_gateway` | StructuredModelGateway, cache, DeepSeek (+ experimental xAI/Anthropic) |
| `adapters/facts` | JSONL / manifest / summary I/O |
| `app/facts` | `facts_from_paths` + CLI wiring |

## Terminal states

Every requirement gets exactly one `RequirementTerminalState` persisted in
`fact_requirement_results.jsonl`:

`RESOLVED` · `CONFIRMED_NONE` · `ABSENT_FROM_SOURCE` · `NOT_APPLICABLE` ·
`NEEDS_MODEL` · `UNRESOLVED_AMBIGUOUS` · `PROVIDER_UNAVAILABLE` ·
`BUDGET_EXHAUSTED` · `FAILED_VALIDATION`

`NEEDS_MODEL` only when: required/source-triggered, authoritative winner for an
allowed domain, strong family cue, bounded window, not CONFIRMED_NONE,
deterministic parse unsafe/missing, and source plausibly contains the answer.

## CLI

```bash
# Deterministic (default)
uv run halyk-agent facts extract \
  --authority work/smoke5c1/authority \
  --covenants work/smoke5d/covenants-polarity \
  --parsed work/smoke541/ocr-enriched \
  --output work/smoke5e1/facts \
  --overwrite

# Opt-in model assist (requires DEEPSEEK_API_KEY + --allow-network-models)
uv run halyk-agent facts extract ... --allow-network-models

# Probe configured providers (never HTTP)
uv run halyk-agent models probe
```

## Organizer / env keys

| Variable | Purpose |
|----------|---------|
| `HALYK_LLM_PRIMARY_PROVIDER` | default `deepseek` |
| `HALYK_LLM_PRIMARY_MODEL` | default `deepseek-v4-flash` |
| `HALYK_LLM_ESCALATION_PROVIDER` | default `deepseek` |
| `HALYK_LLM_ESCALATION_MODEL` | default `deepseek-v4-flash` |
| `HALYK_LLM_MAX_EXTERNAL_ATTEMPTS` | real HTTP budget (default `8`) |
| `HALYK_LLM_MAX_THINKING_ESCALATIONS` | thinking escalations (default `2`) |
| `HALYK_LLM_MAX_CONCURRENCY` | concurrency cap |
| `HALYK_LLM_TEMPERATURE` | default `0` |
| `DEEPSEEK_API_KEY` | DeepSeek runtime key |
| `XAI_API_KEY` | **experimental / disabled** — not auto-selected |
| `ANTHROPIC_API_KEY` | **experimental / disabled** — not auto-selected |

Network remains off unless `--allow-network-models` is passed, even if
`DEEPSEEK_API_KEY` is set.

## Outputs

- `fact_requirements.jsonl`
- `fact_requirement_results.jsonl`
- `fact_candidates.jsonl`
- `accepted_facts.jsonl` / `rejected_facts.jsonl`
- `fact_conflicts.jsonl`
- `model_calls.jsonl` (no secrets; no `reasoning_content`)
- `fact_evidence.jsonl`
- `fact_extraction_manifest.json`
- `fact_extraction_summary.md`

## DeepSeek runtime notes

- Base URL: `https://api.deepseek.com`
- `response_format: {type: json_object}`
- Primary: `thinking: {type: disabled}`
- Escalation: `thinking: {type: enabled}`, `reasoning_effort: high`
- Cache identity includes thinking enabled/disabled + reasoning_effort
- Empty content: at most one in-provider retry; each HTTP counts toward budget
- Prompt forbids calculating unstated FX rates / values
