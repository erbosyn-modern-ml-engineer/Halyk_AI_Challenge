"""Stage 5E structured fact extraction."""

from halyk_agent.domain.fact_extraction.engine import run_fact_extraction
from halyk_agent.domain.fact_extraction.models import (
    DerivationKind,
    FactCandidate,
    FactExtractionReport,
    FactKind,
    FactRecord,
    FactRequirement,
    FactRequirementResult,
    FactValidatorStatus,
    RequirementTerminalState,
)
from halyk_agent.domain.fact_extraction.requirements import derive_fact_requirements

__all__ = [
    "DerivationKind",
    "FactCandidate",
    "FactExtractionReport",
    "FactKind",
    "FactRecord",
    "FactRequirement",
    "FactRequirementResult",
    "FactValidatorStatus",
    "RequirementTerminalState",
    "derive_fact_requirements",
    "run_fact_extraction",
]
