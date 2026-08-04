"""Calculation and policy evaluation contracts."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from halyk_agent.contracts.transactions import EntityLink
from halyk_agent.domain.calculations import CalculatedValue
from halyk_agent.domain.common import JsonObject, NonEmptyStr
from halyk_agent.domain.facts import DerivedFact, ExplicitFact
from halyk_agent.domain.rules import RuleRef
from halyk_agent.domain.transactions import Transaction


class CalculationRequest(BaseModel):
    """Inputs required for a deterministic calculation."""

    model_config = ConfigDict(extra="forbid")

    operation: NonEmptyStr
    algorithm_version: NonEmptyStr
    transactions: list[Transaction] = Field(default_factory=list)
    facts: list[ExplicitFact | DerivedFact] = Field(default_factory=list)
    parameters: JsonObject = Field(default_factory=dict)


class RuleHit(BaseModel):
    """A rule evaluation hit contributing to a decision."""

    model_config = ConfigDict(extra="forbid")

    rule: RuleRef
    passed: bool
    message: NonEmptyStr
    related_fact_ids: list[NonEmptyStr] = Field(default_factory=list)
    related_calculation_ids: list[NonEmptyStr] = Field(default_factory=list)


@runtime_checkable
class CalculationEngine(Protocol):
    """Performs Decimal-safe deterministic calculations (no LLM)."""

    def calculate(self, request: CalculationRequest) -> CalculatedValue:
        """Compute a value and attach a complete calculation trace."""
        ...


@runtime_checkable
class PolicyEngine(Protocol):
    """Evaluates explicit rules against facts and calculations."""

    def evaluate(
        self,
        *,
        facts: list[ExplicitFact | DerivedFact],
        calculations: list[CalculatedValue],
        entity_links: list[EntityLink],
        rule_pack_version: str,
    ) -> list[RuleHit]:
        """Return rule hits for the provided inputs."""
        ...
