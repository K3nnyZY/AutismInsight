import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, text, update

from app.config import Settings
from app.database import Database
from app.errors import ServiceError
from app.models import Conversation, CorpusConfig, Message
from app.schemas import ChatRequest, ChatResponse, MessageView


def bounded_history(messages: list[Message], max_chars: int) -> list[dict]:
    selected = []
    remaining = max_chars
    for message in reversed(messages):
        content = message.content
        if len(content) > remaining:
            break
        selected.append({"role": message.role, "content": content})
        remaining -= len(content)
    return list(reversed(selected))


class ConversationService:
    def __init__(self, database: Database, graph, settings: Settings):
        self.database = database
        self.graph = graph
        self.settings = settings

    async def rate_limit(self, key: str):
        # Database-backed fixed windows work across API workers, unlike an in-memory counter.
        async with self.database.sessions.begin() as session:
            result = await session.scalar(
                text("""
                INSERT INTO rate_buckets (key, minute, hits)
                VALUES (:key, floor(extract(epoch from clock_timestamp()) / 60)::bigint, 1)
                ON CONFLICT (key, minute) DO UPDATE SET hits = rate_buckets.hits + 1
                WHERE rate_buckets.hits < :limit
                RETURNING hits
            """),
                {"key": key, "limit": self.settings.rate_limit_per_minute},
            )
            if result is None:
                raise ServiceError(429, "Has enviado varias solicitudes. Espera un minuto.")

    async def create(self, owner: str) -> UUID:
        async with self.database.sessions.begin() as session:
            item = Conversation(
                owner_hash=owner,
                expires_at=datetime.now(UTC) + timedelta(days=self.settings.session_ttl_days),
            )
            session.add(item)
            await session.flush()
            return item.id

    async def _authorized(self, session, conversation_id: UUID, owner: str, lock=False):
        query = select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.owner_hash == owner,
            Conversation.expires_at > func.now(),
        )
        if lock:
            query = query.with_for_update()
        item = await session.scalar(query)
        if item is None:
            raise ServiceError(404, "La conversación no está disponible.")
        return item

    async def history(self, conversation_id: UUID, owner: str):
        async with self.database.sessions() as session:
            await self._authorized(session, conversation_id, owner)
            messages = list(
                await session.scalars(
                    select(Message)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.created_at.desc(), Message.role.asc())
                    .limit(200)
                )
            )
            return [
                MessageView(
                    id=m.id,
                    role=m.role,
                    content=m.content,
                    created_at=m.created_at,
                    response=ChatResponse.model_validate(m.response) if m.response else None,
                )
                for m in reversed(messages)
            ]

    async def remove(self, conversation_id: UUID, owner: str):
        async with self.database.sessions.begin() as session:
            await self._authorized(session, conversation_id, owner, lock=True)
            await session.execute(delete(Conversation).where(Conversation.id == conversation_id))

    async def respond(self, request: ChatRequest, owner: str) -> ChatResponse:
        payload = request.model_dump(mode="json", exclude={"request_id", "conversation_id"})
        lease = uuid4()
        async with self.database.sessions.begin() as session:
            conversation = await self._authorized(
                session, request.conversation_id, owner, lock=True
            )
            existing = await session.scalar(
                select(Message).where(
                    Message.conversation_id == request.conversation_id,
                    Message.request_id == request.request_id,
                    Message.role == "assistant",
                )
            )
            if existing:
                if existing.request_payload != payload:
                    raise ServiceError(409, "Ese identificador ya pertenece a otra solicitud.")
                return ChatResponse.model_validate(existing.response)
            now = datetime.now(UTC)
            if conversation.active_request_id and conversation.busy_until > now:
                raise ServiceError(409, "Hay una respuesta en curso para esta conversación.")
            if self.graph is None:
                raise ServiceError(503, "El modelo aún no está configurado.")
            signature = await session.scalar(select(CorpusConfig.embedding_signature))
            if signature != self.settings.embedding_signature:
                raise ServiceError(503, "El índice no es compatible con el modelo de embeddings.")
            recent = list(
                await session.scalars(
                    select(Message)
                    .where(Message.conversation_id == request.conversation_id)
                    .order_by(Message.created_at.desc(), Message.role.asc())
                    .limit(self.settings.history_turns * 2)
                )
            )
            history = bounded_history(list(reversed(recent)), self.settings.max_history_chars)
            conversation.active_request_id = lease
            conversation.busy_until = now + timedelta(
                seconds=self.settings.chat_timeout_seconds + 15
            )

        # No database connection is held while the model or embeddings run.
        try:
            async with asyncio.timeout(self.settings.chat_timeout_seconds):
                state = await self.graph.ainvoke(
                    {
                        "question": request.message,
                        "history": history,
                        "audience": request.audience,
                        "style": request.style,
                    },
                    config={"recursion_limit": 20},
                )
            response = ChatResponse(
                message_id=uuid4(),
                request_id=request.request_id,
                answer=state["answer"],
                sources=state["sources"],
                evidence_status=state["evidence_status"],
            )
            async with self.database.sessions.begin() as session:
                conversation = await self._authorized(
                    session, request.conversation_id, owner, lock=True
                )
                if conversation.active_request_id != lease:
                    raise ServiceError(409, "La solicitud anterior expiró. Puedes reintentar.")
                now = datetime.now(UTC)
                session.add(
                    Message(
                        conversation_id=request.conversation_id,
                        request_id=request.request_id,
                        role="user",
                        content=request.message,
                        created_at=now,
                    )
                )
                session.add(
                    Message(
                        id=response.message_id,
                        conversation_id=request.conversation_id,
                        request_id=request.request_id,
                        role="assistant",
                        content=response.answer,
                        response=response.model_dump(mode="json"),
                        request_payload=payload,
                        created_at=now + timedelta(microseconds=1),
                    )
                )
                conversation.active_request_id = None
                conversation.busy_until = None
            return response
        finally:
            # The owner check also prevents a stale request from releasing a newer lease.
            await asyncio.shield(self._release(request.conversation_id, lease))

    async def _release(self, conversation_id: UUID, lease: UUID):
        async with self.database.sessions.begin() as session:
            await session.execute(
                update(Conversation)
                .where(
                    Conversation.id == conversation_id,
                    Conversation.active_request_id == lease,
                )
                .values(active_request_id=None, busy_until=None)
            )
