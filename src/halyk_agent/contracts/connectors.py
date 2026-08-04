"""Source connector contracts."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from halyk_agent.domain.common import JsonObject, NonEmptyStr


class ConnectorCheckpoint(BaseModel):
    """Opaque connector progress marker."""

    model_config = ConfigDict(extra="forbid")

    has_more: bool
    cursor: JsonObject = Field(default_factory=dict)


class ConnectorItem(BaseModel):
    """One ingested source item from an archive or feed."""

    model_config = ConfigDict(extra="forbid")

    item_id: NonEmptyStr
    source_path: NonEmptyStr
    media_type: NonEmptyStr | None = None
    content_hash: NonEmptyStr | None = None
    metadata: JsonObject = Field(default_factory=dict)


class ConnectorFailure(BaseModel):
    """Structured connector failure for a single item or entity."""

    model_config = ConfigDict(extra="forbid")

    item_id: NonEmptyStr | None = None
    message: NonEmptyStr
    retryable: bool = False


class ConnectorBatch(BaseModel):
    """Batch boundary emitted by a source connector."""

    model_config = ConfigDict(extra="forbid")

    items: list[ConnectorItem] = Field(default_factory=list)
    failures: list[ConnectorFailure] = Field(default_factory=list)
    checkpoint: ConnectorCheckpoint


@runtime_checkable
class SourceConnector(Protocol):
    """Loads competition archives or incremental source updates in batches."""

    def load_from_state(
        self,
        *,
        checkpoint: ConnectorCheckpoint | None = None,
    ) -> AsyncIterator[ConnectorBatch]:
        """Yield batches that reconstruct the current source state."""
        ...

    def poll_source(
        self,
        *,
        start: datetime,
        end: datetime,
        checkpoint: ConnectorCheckpoint | None = None,
    ) -> AsyncIterator[ConnectorBatch]:
        """Yield batches for changes within an inclusive time window."""
        ...

    async def retrieve_slim_ids(self) -> Sequence[str]:
        """Return source item IDs for deletion / existence checks."""
        ...
