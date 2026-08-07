"""Stage 5E structured fact extraction."""

from __future__ import annotations

from typing import Any

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


def __getattr__(name: str) -> Any:
    if name in {
        "DerivationKind",
        "FactCandidate",
        "FactExtractionReport",
        "FactKind",
        "FactRecord",
        "FactRequirement",
        "FactRequirementResult",
        "FactValidatorStatus",
        "RequirementTerminalState",
    }:
        from halyk_agent.domain.fact_extraction import models as _models

        return getattr(_models, name)
    if name == "derive_fact_requirements":
        from halyk_agent.domain.fact_extraction.requirements import derive_fact_requirements

        return derive_fact_requirements
    if name == "run_fact_extraction":
        from halyk_agent.domain.fact_extraction.engine import run_fact_extraction

        return run_fact_extraction
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
