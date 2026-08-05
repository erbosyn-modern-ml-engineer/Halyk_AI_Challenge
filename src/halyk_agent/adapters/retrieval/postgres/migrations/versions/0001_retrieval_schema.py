"""Initial retrieval schema: BYTEA embeddings (postgres_numpy_exact).

Revision ID: 0001_retrieval_schema
Revises:
Create Date: 2026-08-05

Docker-free default: float32 vectors in BYTEA. Does not CREATE EXTENSION vector.
Optional pgvector remains a separate runtime path when the extension already exists.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_retrieval_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Intentionally no CREATE EXTENSION vector — postgres_numpy_exact is authoritative.
    op.create_table(
        "retrieval_chunks",
        sa.Column("chunk_id", sa.Text(), primary_key=True, nullable=False),
        sa.Column("document_id", sa.Text(), nullable=False),
        sa.Column("document_version_id", sa.Text(), nullable=False),
        sa.Column("artifact_id", sa.Text(), nullable=False),
        sa.Column("source_file", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("level", sa.String(length=64), nullable=False),
        sa.Column("page_numbers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("retrieval_text", sa.Text(), nullable=False),
        sa.Column("retrieval_text_kind", sa.String(length=64), nullable=False),
        sa.Column("parent_chunk_id", sa.Text(), nullable=True),
        sa.Column(
            "source_block_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "source_table_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "evidence_span_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("heading_path", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("character_count", sa.Integer(), nullable=False),
        sa.Column("estimated_token_count", sa.Integer(), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    )
    op.create_index(
        "ix_retrieval_chunks_document_id",
        "retrieval_chunks",
        ["document_id"],
    )
    op.create_index(
        "ix_retrieval_chunks_document_version_id",
        "retrieval_chunks",
        ["document_version_id"],
    )
    op.create_index(
        "ix_retrieval_chunks_artifact_id",
        "retrieval_chunks",
        ["artifact_id"],
    )
    op.create_index(
        "ix_retrieval_chunks_source_file",
        "retrieval_chunks",
        ["source_file"],
    )
    op.create_index("ix_retrieval_chunks_kind", "retrieval_chunks", ["kind"])
    op.create_index("ix_retrieval_chunks_level", "retrieval_chunks", ["level"])
    op.execute(
        """
        CREATE INDEX ix_retrieval_chunks_fts
        ON retrieval_chunks
        USING GIN (to_tsvector('simple', retrieval_text))
        """
    )

    op.create_table(
        "retrieval_embeddings",
        sa.Column("chunk_id", sa.Text(), nullable=False),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("model_revision", sa.Text(), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("embedding_blob", sa.LargeBinary(), nullable=False),
        sa.Column("vector_checksum", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("chunk_id", "model_id", "model_revision"),
        sa.UniqueConstraint(
            "chunk_id",
            "model_id",
            "model_revision",
            name="uq_retrieval_embeddings_chunk_model_revision",
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["retrieval_chunks.chunk_id"],
            ondelete="CASCADE",
        ),
    )

    op.create_table(
        "retrieval_indexes",
        sa.Column("index_key", sa.Text(), primary_key=True, nullable=False),
        sa.Column(
            "index_identity",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "embedding_model",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "ready",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("vector_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "vector_backend",
            sa.String(length=64),
            nullable=False,
            server_default="postgres_numpy_exact",
        ),
    )


def downgrade() -> None:
    op.drop_table("retrieval_indexes")
    op.drop_table("retrieval_embeddings")
    op.execute("DROP INDEX IF EXISTS ix_retrieval_chunks_fts")
    op.drop_index("ix_retrieval_chunks_level", table_name="retrieval_chunks")
    op.drop_index("ix_retrieval_chunks_kind", table_name="retrieval_chunks")
    op.drop_index("ix_retrieval_chunks_source_file", table_name="retrieval_chunks")
    op.drop_index("ix_retrieval_chunks_artifact_id", table_name="retrieval_chunks")
    op.drop_index(
        "ix_retrieval_chunks_document_version_id",
        table_name="retrieval_chunks",
    )
    op.drop_index("ix_retrieval_chunks_document_id", table_name="retrieval_chunks")
    op.drop_table("retrieval_chunks")
