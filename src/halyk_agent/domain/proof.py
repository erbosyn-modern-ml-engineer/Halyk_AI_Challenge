"""Proof bundle assembling evidence-backed decision artifacts."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from halyk_agent.domain.calculations import CalculatedValue
from halyk_agent.domain.common import NonEmptyStr
from halyk_agent.domain.decisions import DecisionResult, DecisionStatus
from halyk_agent.domain.documents import ApplicableVersionSet
from halyk_agent.domain.facts import DerivedFact, ExplicitFact
from halyk_agent.domain.rules import RuleRef


class ProofBundle(BaseModel):
    """Complete proof package for an internal decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: NonEmptyStr
    decision: DecisionResult
    applicable_versions: ApplicableVersionSet
    explicit_facts: list[ExplicitFact] = Field(default_factory=list)
    derived_facts: list[DerivedFact] = Field(default_factory=list)
    calculations: list[CalculatedValue] = Field(default_factory=list)
    rules: list[RuleRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_completeness(self) -> ProofBundle:
        if not self.applicable_versions.versions:
            raise ValueError("completed decision requires at least one applicable version")
        if (
            self.decision.status in {DecisionStatus.APPROVE, DecisionStatus.REJECT}
            and not self.rules
        ):
            raise ValueError(f"{self.decision.status.value} decision requires at least one rule")
        return self
