"""Stage 5E fact extraction constants."""

from __future__ import annotations

FACT_SCHEMA_VERSION = "halyk.facts.schema.v4"
FACT_EXTRACTOR_VERSION = "halyk.facts.extractor.v4"
FACT_VALIDATOR_VERSION = "halyk.facts.validator.v4"
FACT_REQUIREMENT_VERSION = "halyk.facts.requirements.v4"
FACT_ALGORITHM_VERSION = "halyk.facts.algorithm.v4"

MODEL_GATEWAY_VERSION = "halyk.models.gateway.v3"
MODEL_PROMPT_VERSION = "halyk.models.prompt.v3"
MODEL_CACHE_VERSION = "halyk.models.cache.v3"

# Local cache/epoch identity only — never sent as the API model id.
DEFAULT_PROVIDER_REVISION = "deepseek-v4-flash-2026-07-31"
DEFAULT_DEEPSEEK_MAX_TOKENS = 2048
