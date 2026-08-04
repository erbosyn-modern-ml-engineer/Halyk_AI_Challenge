"""Transaction loading and entity resolution contracts."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from halyk_agent.domain.common import NonEmptyStr
from halyk_agent.domain.facts import DerivedFact, ExplicitFact
from halyk_agent.domain.transactions import Transaction


class EntityLink(BaseModel):
    """Deterministic link between a fact subject and a transaction entity."""

    model_config = ConfigDict(extra="forbid")

    fact_id: NonEmptyStr
    transaction_id: NonEmptyStr
    entity_id: NonEmptyStr
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    method: NonEmptyStr


@runtime_checkable
class TransactionSource(Protocol):
    """Loads transactions from competition archive artifacts."""

    async def load(self, source_path: str) -> list[Transaction]:
        """Parse and return Decimal-safe transactions from a source path."""
        ...


@runtime_checkable
class EntityResolver(Protocol):
    """Links facts to transactions and entities."""

    def resolve(
        self,
        *,
        facts: list[ExplicitFact | DerivedFact],
        transactions: list[Transaction],
    ) -> list[EntityLink]:
        """Return deterministic entity links for the provided inputs."""
        ...
