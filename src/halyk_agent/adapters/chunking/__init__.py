"""Structure-aware chunking adapters."""

from __future__ import annotations

from halyk_agent.adapters.chunking.cache import LocalChunkCache, cache_key
from halyk_agent.adapters.chunking.structure_chunker import (
    CHUNKER_NAME,
    CHUNKER_VERSION,
    ChunkerConfig,
    StructureAwareChunker,
    build_chunk_manifest,
    build_chunker_identity,
)

__all__ = [
    "CHUNKER_NAME",
    "CHUNKER_VERSION",
    "ChunkerConfig",
    "LocalChunkCache",
    "StructureAwareChunker",
    "build_chunk_manifest",
    "build_chunker_identity",
    "cache_key",
]
