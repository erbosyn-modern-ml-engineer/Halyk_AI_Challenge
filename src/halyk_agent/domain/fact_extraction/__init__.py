"""Stage 5E structured fact extraction."""

from halyk_agent.domain.fact_extraction.engine import run_fact_extraction
from halyk_agent.domain.fact_extraction.models import (
    FactCandidate,
    FactExtractionReport,
    FactKind,
    FactRecord,
    FactRequirement,
    FactValidatorStatus,
)
from halyk_agent.domain.fact_extraction.requirements import derive_fact_requirements

__all__ = [
    "FactCandidate",
    "FactExtractionReport",
    "FactKind",
    "FactRecord",
    "FactRequirement",
    "FactValidatorStatus",
    "derive_fact_requirements",
    "run_fact_extraction",
]
