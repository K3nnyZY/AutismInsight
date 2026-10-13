"""Revisión 001: corpus, índices de recuperación, conversaciones y contadores de tráfico."""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

from app.config import get_settings

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    """Activa pgvector y crea tablas, restricciones, índices HNSW/GIN y firma inicial.

    Los mensajes y fragmentos tienen borrado en cascada. search_vector se
    calcula en PostgreSQL; rate_buckets se consulta con SQL directo desde el servicio.
    """
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "corpus_config",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("embedding_signature", sa.String(200), nullable=False),
    )
    corpus = sa.table("corpus_config", sa.column("id"), sa.column("embedding_signature"))
    op.bulk_insert(corpus, [{"id": 1, "embedding_signature": get_settings().embedding_signature}])
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("source_key", sa.String(300), nullable=False, unique=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("source_url", sa.Text),
        sa.Column("trust_level", sa.String(16), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("trust_level IN ('reviewed', 'demo')"),
    )
    op.create_table(
        "chunks",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("document_id", sa.Uuid, sa.ForeignKey("documents.id", ondelete="CASCADE")),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("page", sa.Integer),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("embedding", Vector(1536), nullable=False),
    )
    op.execute("""
        ALTER TABLE chunks ADD COLUMN search_vector tsvector
        GENERATED ALWAYS AS (to_tsvector('spanish'::regconfig, content)) STORED
    """)
    op.create_index("chunks_document_idx", "chunks", ["document_id"])
    op.create_index(
        "chunks_hnsw_idx",
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index("chunks_fts_idx", "chunks", ["search_vector"], postgresql_using="gin")
    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("owner_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active_request_id", sa.Uuid),
        sa.Column("busy_until", sa.DateTime(timezone=True)),
    )
    op.create_index("conversations_owner_idx", "conversations", ["owner_hash"])
    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Uuid,
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("request_id", sa.Uuid, nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("response", JSONB),
        sa.Column("request_payload", JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("conversation_id", "request_id", "role"),
        sa.CheckConstraint("role IN ('user', 'assistant')"),
    )
    op.create_index("messages_conversation_idx", "messages", ["conversation_id"])
    op.create_table(
        "rate_buckets",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("minute", sa.BigInteger, primary_key=True),
        sa.Column("hits", sa.Integer, nullable=False),
    )


def downgrade():
    """Revierte la revisión eliminando las tablas en orden de dependencia.

    Es una operación destructiva: elimina los datos de esas tablas.
    """
    for table in (
        "rate_buckets",
        "messages",
        "conversations",
        "chunks",
        "documents",
        "corpus_config",
    ):
        op.drop_table(table)
