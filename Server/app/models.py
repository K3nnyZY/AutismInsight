"""Modelos ORM: describen las tablas que Alembic crea en PostgreSQL.

Los objetos de estas clases representan filas, no peticiones HTTP.
rate_buckets se administra mediante SQL directo y no tiene clase ORM.
"""

from datetime import datetime
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import Computed, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import EMBEDDING_DIMENSIONS


class Base(DeclarativeBase):
    """Base declarativa que reúne los metadatos de las tablas para SQLAlchemy y Alembic."""

    pass


class CorpusConfig(Base):
    """Firma del modelo y dimensión usados para indexar el corpus de conocimiento."""

    __tablename__ = "corpus_config"
    id: Mapped[int] = mapped_column(primary_key=True)
    embedding_signature: Mapped[str] = mapped_column(String(200))


class Document(Base):
    """Fuente completa con título, procedencia, nivel de revisión y huella de contenido.

    source_key identifica una fuente estable al actualizarla; fingerprint
    detecta si su contenido, metadatos o configuración de ingesta cambiaron.
    """

    __tablename__ = "documents"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    source_key: Mapped[str] = mapped_column(String(300), unique=True)
    title: Mapped[str] = mapped_column(String(300))
    source_url: Mapped[str | None] = mapped_column(Text)
    trust_level: Mapped[str] = mapped_column(String(16))
    fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Chunk(Base):
    """Fragmento recuperable de un documento, con página, posición y embedding.

    PostgreSQL calcula search_vector a partir del texto para la búsqueda
    por palabras. Eliminar el documento elimina sus fragmentos en cascada.
    """

    __tablename__ = "chunks"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    content: Mapped[str] = mapped_column(Text)
    page: Mapped[int | None]
    position: Mapped[int]
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('spanish'::regconfig, content)", persisted=True)
    )


class Conversation(Base):
    """Sesión con propietario, vencimiento y concesión temporal de generación.

    owner_hash es el hash de la cookie, no su valor original. active_request_id
    identifica la concesión interna; busy_until permite recuperarse si un worker muere.
    """

    __tablename__ = "conversations"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    active_request_id: Mapped[UUID | None]
    busy_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Message(Base):
    """Mensaje persistido y, para el asistente, respuesta completa y petición original.

    La restricción (conversation_id, request_id, role) evita duplicar un turno.
    response guarda las citas y el estado de evidencia; request_payload permite
    rechazar un reintento que reutiliza el identificador con otro contenido.
    """

    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("conversation_id", "request_id", "role"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    request_id: Mapped[UUID]
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    response: Mapped[dict | None] = mapped_column(JSONB)
    request_payload: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
