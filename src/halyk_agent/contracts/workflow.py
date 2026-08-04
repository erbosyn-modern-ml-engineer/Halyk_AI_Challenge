"""Decision workflow and proof assembly contracts."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from halyk_agent.domain.calculations import CalculatedValue
from halyk_agent.domain.common import JsonObject, NonEmptyStr
from halyk_agent.domain.decisions import DecisionResult
from halyk_agent.domain.documents import ApplicableVersionSet
from halyk_agent.domain.facts import DerivedFact, ExplicitFact
from halyk_agent.domain.proof import ProofBundle
from halyk_agent.domain.rules import RuleRef
from halyk_agent.profiles import ProfileName


class WorkflowInput(BaseModel):
    """Case input for a decision workflow run."""

    model_config = ConfigDict(extra="forbid")

    case_id: NonEmptyStr
    profile: ProfileName
    archive_path: NonEmptyStr | None = None
    parameters: JsonObject = Field(default_factory=dict)


class WorkflowState(BaseModel):
    """Serializable workflow state snapshot."""

    model_config = ConfigDict(extra="forbid")

    case_id: NonEmptyStr
    status: NonEmptyStr
    decision: DecisionResult | None = None
    proof: ProofBundle | None = None
    metadata: JsonObject = Field(default_factory=dict)


@runtime_checkable
class DecisionWorkflow(Protocol):
    """Orchestrates the shared decision pipeline for a profile."""

    async def run(self, workflow_input: WorkflowInput) -> WorkflowState:
        """Execute the decision workflow to completion or interrupt."""
        ...

    async def resume(self, case_id: str, *, command: JsonObject) -> WorkflowState:
        """Resume an interrupted workflow with an external command."""
        ...


@runtime_checkable
class ProofBundleBuilder(Protocol):
    """Assembles a proof bundle from decision artifacts."""

    def build(
        self,
        *,
        case_id: str,
        decision: DecisionResult,
        applicable_versions: ApplicableVersionSet,
        explicit_facts: list[ExplicitFact],
        derived_facts: list[DerivedFact],
        calculations: list[CalculatedValue],
        rules: list[RuleRef],
    ) -> ProofBundle:
        """Build a validated proof bundle for the case."""
        ...
