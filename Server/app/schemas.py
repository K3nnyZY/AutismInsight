from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

Audience = Literal["person", "family", "professional"]
Style = Literal["simple", "standard", "detailed"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConversationCreate(StrictModel):
    consent: bool


class ChatRequest(StrictModel):
    conversation_id: UUID
    request_id: UUID
    message: str = Field(min_length=1, max_length=4000)
    audience: Audience = "person"
    style: Style = "standard"

    @field_validator("message")
    @classmethod
    def strip_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Escribe una pregunta.")
        return value.strip()


class Source(BaseModel):
    citation: int
    document_id: UUID
    chunk_id: UUID
    title: str
    page: int | None
    url: str | None
    trust_level: Literal["reviewed", "demo"]
    excerpt: str


class ChatResponse(BaseModel):
    message_id: UUID
    request_id: UUID
    answer: str
    sources: list[Source]
    evidence_status: Literal["grounded", "insufficient", "not_applicable"]


class MessageView(BaseModel):
    id: UUID
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime
    response: ChatResponse | None = None
