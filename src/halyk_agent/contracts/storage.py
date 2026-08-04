"""Object storage and canonical document store contracts."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from halyk_agent.domain.common import JsonObject, NonEmptyStr
from halyk_agent.domain.documents import DocumentVersionRef


class StoredObjectRef(BaseModel):
    """Reference to bytes persisted in object storage."""

    model_config = ConfigDict(extra="forbid")

    key: NonEmptyStr
    content_hash: NonEmptyStr
    media_type: NonEmptyStr | None = None
    size_bytes: int = Field(ge=0)


class CanonicalDocument(BaseModel):
    """Normalized document record independent of parser backend."""

    model_config = ConfigDict(extra="forbid")

    document_id: NonEmptyStr
    version: DocumentVersionRef
    title: NonEmptyStr | None = None
    text: NonEmptyStr | None = None
    metadata: JsonObject = Field(default_factory=dict)


@runtime_checkable
class ObjectStore(Protocol):
    """Stores raw source bytes addressed by content-addressable keys."""

    async def put(self, key: str, data: bytes, *, media_type: str | None = None) -> StoredObjectRef:
        """Persist bytes and return a storage reference."""
        ...

    async def get(self, key: str) -> bytes:
        """Load bytes by key."""
        ...

    async def delete(self, key: str) -> None:
        """Delete bytes by key if present."""
        ...


@runtime_checkable
class CanonicalDocumentStore(Protocol):
    """Persists and retrieves canonical documents."""

    async def upsert(self, document: CanonicalDocument) -> NonEmptyStr:
        """Insert or update a canonical document and return its ID."""
        ...

    async def get(self, document_id: str) -> CanonicalDocument:
        """Load a canonical document by ID."""
        ...

    async def get_by_content_hash(self, content_hash: str) -> CanonicalDocument | None:
        """Return a document matching the content hash, if any."""
        ...
