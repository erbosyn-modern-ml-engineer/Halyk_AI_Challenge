"""Version constants for Stage 5C taxonomy/authority rules."""

# Multilingual lifecycle-banner literals are intentional.
# ruff: noqa: RUF001

from __future__ import annotations

AUTHORITY_SCHEMA_VERSION = "halyk.authority_manifest.v1"
TAXONOMY_RULE_VERSION = "halyk.taxonomy.rules.v1.2"
AUTHORITY_RULE_VERSION = "halyk.authority.rules.v1.2"
AUTHORITY_ALGORITHM_VERSION = "halyk.authority.v1"

# Lifecycle status-banner vocabulary. Accepted only in banner form — see
# ``halyk_agent.domain.authority.evidence.is_status_banner``.
SUPERSESSION_BANNER_PATTERNS: tuple[str, ...] = (
    "НЕДЕЙСТВУЮЩАЯ РЕДАКЦИЯ",
    "НЕ ПРИМЕНЯЕТСЯ",
    "заменена окончательным",
    "заменена и изложена",
    "күші жойылған",
    "superseded",
    "obsolete",
    "no longer in force",
    "not in force",
)
