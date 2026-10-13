"""Contratos Pydantic para validar entradas HTTP y serializar respuestas.

Estos esquemas no crean tablas. FastAPI los utiliza también para generar /docs.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

Audience = Literal["person", "family", "professional"]
Style = Literal["simple", "standard", "detailed"]


class StrictModel(BaseModel):
    """Base para entradas que rechaza campos adicionales no declarados."""

    model_config = ConfigDict(extra="forbid")


class ConversationCreate(StrictModel):
    """Entrada de creación: exige que el cliente declare si acepta el procesamiento."""

    consent: bool


class ChatRequest(StrictModel):
    """Pregunta asociada a una conversación, con identificador de reintento y preferencias."""

    conversation_id: UUID
    request_id: UUID
    message: str = Field(min_length=1, max_length=4000)
    audience: Audience = "person"
    style: Style = "standard"

    @field_validator("message")
    @classmethod
    def strip_message(cls, value: str) -> str:
        """Rechaza preguntas compuestas solo por espacios y recorta espacios exteriores."""
        if not value.strip():
            raise ValueError("Escribe una pregunta.")
        return value.strip()


class Source(BaseModel):
    """Referencia mostrable: ID de cita, documento, fragmento, página, URL y extracto."""

    citation: int
    document_id: UUID
    chunk_id: UUID
    title: str
    page: int | None
    url: str | None
    trust_level: Literal["reviewed", "demo"]
    excerpt: str


class ChatResponse(BaseModel):
    """Respuesta final del chat, con fuentes y estado de respaldo documental."""

    message_id: UUID
    request_id: UUID
    answer: str
    sources: list[Source]
    evidence_status: Literal["grounded", "insufficient", "not_applicable"]


class MessageView(BaseModel):
    """Representación de un mensaje del historial, incluida su respuesta si es del asistente."""

    id: UUID
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime
    response: ChatResponse | None = None
