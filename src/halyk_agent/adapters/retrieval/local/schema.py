"""SQLite DDL for the FAST local hybrid retrieval index."""

from __future__ import annotations

# FTS5 unicode61 tokenizer without English Porter stemming (language-neutral).
FTS_TOKENIZE = "unicode61"

CHUNKS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY NOT NULL,
    document_id TEXT NOT NULL,
    document_version_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    source_file TEXT NOT NULL,
    kind TEXT NOT NULL,
    level TEXT NOT NULL,
    page_numbers_json TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    retrieval_text TEXT NOT NULL,
    retrieval_text_kind TEXT NOT NULL,
    parent_chunk_id TEXT,
    source_block_ids_json TEXT NOT NULL,
    source_table_ids_json TEXT NOT NULL,
    evidence_span_ids_json TEXT NOT NULL,
    heading_path_json TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    character_count INTEGER NOT NULL,
    estimated_token_count INTEGER NOT NULL,
    metadata_json TEXT NOT NULL
)
"""

CHUNK_METADATA_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS chunk_metadata (
    chunk_id TEXT PRIMARY KEY NOT NULL
        REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    document_id TEXT NOT NULL,
    document_version_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    source_file TEXT NOT NULL,
    kind TEXT NOT NULL,
    level TEXT NOT NULL,
    page_numbers_json TEXT NOT NULL
)
"""

EMBEDDING_RECORDS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS embedding_records (
    chunk_id TEXT PRIMARY KEY NOT NULL
        REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    model_id TEXT NOT NULL,
    model_revision TEXT NOT NULL,
    dimension INTEGER NOT NULL,
    vector_blob BLOB NOT NULL,
    vector_checksum TEXT NOT NULL
)
"""

INDEX_METADATA_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS index_metadata (
    key TEXT PRIMARY KEY NOT NULL,
    value_json TEXT NOT NULL
)
"""

# Virtual FTS5 table: content indexed for BM25; chunk_id stored unindexed for joins.
CHUNKS_FTS_SQL = f"""
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED,
    retrieval_text,
    tokenize = '{FTS_TOKENIZE}'
)
"""

CHUNK_METADATA_INDEXES_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_chunk_metadata_document_id ON chunk_metadata(document_id)",
    "CREATE INDEX IF NOT EXISTS idx_chunk_metadata_document_version_id "
    "ON chunk_metadata(document_version_id)",
    "CREATE INDEX IF NOT EXISTS idx_chunk_metadata_artifact_id ON chunk_metadata(artifact_id)",
    "CREATE INDEX IF NOT EXISTS idx_chunk_metadata_source_file ON chunk_metadata(source_file)",
    "CREATE INDEX IF NOT EXISTS idx_chunk_metadata_kind ON chunk_metadata(kind)",
    "CREATE INDEX IF NOT EXISTS idx_chunk_metadata_level ON chunk_metadata(level)",
)

SCHEMA_STATEMENTS: tuple[str, ...] = (
    CHUNKS_TABLE_SQL,
    CHUNK_METADATA_TABLE_SQL,
    EMBEDDING_RECORDS_TABLE_SQL,
    INDEX_METADATA_TABLE_SQL,
    CHUNKS_FTS_SQL,
    *CHUNK_METADATA_INDEXES_SQL,
)

INDEX_META_KEY_IDENTITY = "index_identity"
INDEX_META_KEY_EMBEDDING_MODEL = "embedding_model"
INDEX_META_KEY_READY = "ready"
INDEX_META_KEY_VECTOR_COUNT = "vector_count"
INDEX_META_KEY_CHUNK_COUNT = "chunk_count"

__all__ = [
    "CHUNKS_FTS_SQL",
    "CHUNKS_TABLE_SQL",
    "CHUNK_METADATA_INDEXES_SQL",
    "CHUNK_METADATA_TABLE_SQL",
    "EMBEDDING_RECORDS_TABLE_SQL",
    "FTS_TOKENIZE",
    "INDEX_METADATA_TABLE_SQL",
    "INDEX_META_KEY_CHUNK_COUNT",
    "INDEX_META_KEY_EMBEDDING_MODEL",
    "INDEX_META_KEY_IDENTITY",
    "INDEX_META_KEY_READY",
    "INDEX_META_KEY_VECTOR_COUNT",
    "SCHEMA_STATEMENTS",
]
