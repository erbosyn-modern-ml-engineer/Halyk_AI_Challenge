# Stage 5E — Structured Fact Extraction

**Status:** `STAGE_5E_3_OWNERSHIP_GUARD` (ownership semantic table context; DeepSeek wired offline; no live API verification in CI)

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
   ExternalAttemptBudget claim-BEFORE each HTTP (retries/escalation)
   JSON example + max_tokens in request; usage/latency on ModelCallRecord
   cache identity includes provider_revision/cache_epoch + validator_version
```

**Defaults:** deterministic-only, `allow_network=False`, fail-closed.
No ledger mutation. No covenant actuals (Stage 5F). No `ground_truth.json` reads.

### Stage 5E.3 ownership semantic guard

Ownership percentages require **local** table/section context meaning
ownership/voting rights (e.g. «Доля голосующих прав»). Collateral / pledged-asset
tables (e.g. «Доля активов в залоге») must never emit `OwnershipPayload`.
Nearest preceding header wins; mojibake-normalized headers use the same gate.
Quoted bare legal forms (`"LLP"`, `«ТОО»`) are rejected. DeepSeek JSON examples
are synthetic only (no public corpus entities/amounts).

### Stage 5E.2 closure notes

- Rejected reclassifications (proposal/review + original-remains) are facts with
  `disposition=REJECTED`, not `CONFIRMED_NONE`. Generic “не требовалось” stays
  `CONFIRMED_NONE`. One requirement may resolve to multiple facts.
- Ownership rows support quoted names (`"Name" LLP`, `«Name» LLP`) and keep legal form.
- FX `SOURCE_TRIGGERED` requires a concrete FX **event** cue — not Note 5 policy boilerplate.
- Subsidiary model eligibility requires subsidiary/дочерн/restricted language — bare
  “групп”/“group” is insufficient (`ABSENT_FROM_SOURCE`).
- Selected NEEDS_MODEL windows must themselves contain answer-capable evidence.
- API model id remains `deepseek-v4-flash`; `HALYK_LLM_PROVIDER_REVISION` (default
  `deepseek-v4-flash-2026-07-31`) is cache identity only.

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
| `HALYK_LLM_MAX_TOKENS` | DeepSeek output cap (default `2048`) |
| `HALYK_LLM_PROVIDER_REVISION` | cache epoch only (default `deepseek-v4-flash-2026-07-31`) |
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
- API model: `deepseek-v4-flash` (revision/cache epoch is separate metadata)
- `response_format: {type: json_object}`
- Prompt includes fact-kind-specific literal JSON example + `max_tokens`
- Primary: `thinking: {type: disabled}`
- Escalation: `thinking: {type: enabled}`, `reasoning_effort: high`
- Cache identity includes thinking mode, `max_tokens`, `provider_revision`, validator version
- Empty content: at most one in-provider retry; each HTTP claims budget **before** the request
- `ModelCallRecord` stores `latency_ms` + tolerant token usage (cache/reasoning optional)
- Prompt forbids calculating unstated FX rates / values
- Do not migrate to Responses API in Stage 5E
