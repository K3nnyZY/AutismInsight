"""Reglas de conversaciones: acceso, historial, concurrencia, reintentos y persistencia."""

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
    """Conserva mensajes recientes completos dentro de un presupuesto de caracteres.

    Recibe mensajes en orden cronológico, recorre desde el más reciente
    y se detiene ante el primero que no cabe; devuelve el tramo en el orden original.
    """
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
    """Coordina PostgreSQL y LangGraph sin acoplar las reglas de negocio a HTTP."""

    def __init__(self, database: Database, graph, settings: Settings):
        """Recibe las dependencias; graph puede ser None si el proveedor no está configurado."""
        self.database = database
        self.graph = graph
        self.settings = settings

    async def rate_limit(self, key: str):
        # El contador vive en PostgreSQL para compartir el límite entre procesos de la API.
        """Cuenta solicitudes por clave en ventanas de un minuto compartidas entre workers.

        El INSERT con ON CONFLICT incrementa de forma atómica. Si la ventana
        alcanzó el límite, no devuelve una fila y se lanza ServiceError 429.
        """
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
        """Guarda una conversación para el hash del propietario y devuelve su UUID.

        El vencimiento se calcula al crearla; enviar mensajes no renueva ese plazo.
        """
        async with self.database.sessions.begin() as session:
            item = Conversation(
                owner_hash=owner,
                expires_at=datetime.now(UTC) + timedelta(days=self.settings.session_ttl_days),
            )
            session.add(item)
            await session.flush()
            return item.id

    async def _authorized(self, session, conversation_id: UUID, owner: str, lock=False):
        """Obtiene una conversación del propietario si todavía no ha vencido.

        Con lock=True bloquea la fila durante la transacción para serializar
        actualizaciones. Un recurso ajeno, ausente o vencido devuelve el mismo 404.
        """
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
        """Autoriza el acceso y convierte los últimos 200 mensajes a MessageView.

        La consulta los obtiene del más reciente al más antiguo y luego invierte
        el resultado para devolverlos en orden de conversación.
        """
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
        """Bloquea y elimina la conversación; la clave foránea borra mensajes en cascada."""
        async with self.database.sessions.begin() as session:
            await self._authorized(session, conversation_id, owner, lock=True)
            await session.execute(delete(Conversation).where(Conversation.id == conversation_id))

    async def respond(self, request: ChatRequest, owner: str) -> ChatResponse:
        """Genera o recupera una respuesta idempotente y guarda el turno completo.

        Primera transacción: autoriza, comprueba reintentos y toma una concesión
        temporal. Fuera de la transacción: ejecuta el grafo con timeout e historial
        acotado. Segunda transacción: verifica que sigue siendo dueño de la concesión
        y guarda juntos usuario y asistente. finally libera su concesión si aún existe.

        request.request_id identifica el turno del cliente; lease es otro UUID interno
        que impide que una ejecución antigua interfiera con una nueva. Reutilizar el
        request_id con otra pregunta o preferencias produce 409. También produce 409
        si hay una generación vigente o la concesión fue reemplazada.
        """
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

        # La concesión ya está guardada; se libera la conexión antes de llamar a los modelos.
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
            # El UUID de la concesión evita que una ejecución antigua libere la de otra petición.
            await asyncio.shield(self._release(request.conversation_id, lease))

    async def _release(self, conversation_id: UUID, lease: UUID):
        """Libera solo la concesión cuyo UUID sigue coincidiendo con esta ejecución.

        Así una respuesta tardía no libera el trabajo de otro worker. respond
        utiliza asyncio.shield para proteger la limpieza frente a cancelaciones.
        """
        async with self.database.sessions.begin() as session:
            await session.execute(
                update(Conversation)
                .where(
                    Conversation.id == conversation_id,
                    Conversation.active_request_id == lease,
                )
                .values(active_request_id=None, busy_until=None)
            )
