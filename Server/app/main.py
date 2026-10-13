import hashlib
import logging
import re
import secrets
import time
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from app.config import Settings, get_settings
from app.database import Database
from app.errors import ServiceError
from app.graph import build_graph
from app.middleware import BodySizeLimit
from app.models import Document
from app.providers import StructuredModels, create_embeddings
from app.retrieval import HybridRetriever
from app.schemas import ChatRequest, ChatResponse, ConversationCreate, MessageView
from app.service import ConversationService

logger = logging.getLogger(__name__)
COOKIE = "autism_insight_session"


def owner_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def get_owner(request: Request) -> str:
    token = request.cookies.get(COOKIE, "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise ServiceError(401, "Inicia una conversación para continuar.")
    return owner_hash(token)


def create_app(settings: Settings | None = None, service=None):
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application):
        if service is not None:
            application.state.service = service
            yield
            return
        database = Database(settings)
        graph = None
        if settings.openai_api_key.get_secret_value():
            graph = build_graph(
                StructuredModels(settings),
                HybridRetriever(database, create_embeddings(settings), settings),
                settings,
            )
        application.state.service = ConversationService(database, graph, settings)
        try:
            yield
        finally:
            await database.close()

    application = FastAPI(title="Autism Insight", version="0.1.0", lifespan=lifespan)
    application.add_middleware(BodySizeLimit, limit=16384)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @application.middleware("http")
    async def boundaries(request: Request, call_next):
        started = time.monotonic()
        origin = request.headers.get("origin")
        if request.method in {"POST", "DELETE"}:
            if origin and origin not in settings.cors_origins:
                return JSONResponse({"detail": "Origen no permitido."}, status_code=403)
            if not origin and settings.environment == "production":
                return JSONResponse({"detail": "Se requiere un origen permitido."}, status_code=403)
            length = request.headers.get("content-length", "0")
            if not length.isdecimal() or int(length) > 16384:
                return JSONResponse({"detail": "Solicitud demasiado grande."}, status_code=413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        logger.info(
            "http_request method=%s status=%s duration_ms=%d",
            request.method,
            response.status_code,
            int((time.monotonic() - started) * 1000),
        )
        return response

    @application.exception_handler(ServiceError)
    async def service_error(request, error):
        headers = {"Retry-After": "60"} if error.status == 429 else None
        return JSONResponse({"detail": error.detail}, status_code=error.status, headers=headers)

    @application.exception_handler(TimeoutError)
    async def timeout_error(request, error):
        return JSONResponse(
            {"detail": "La respuesta tardó demasiado. Puedes reintentar."}, status_code=504
        )

    @application.exception_handler(Exception)
    async def unexpected_error(request, error):
        # Avoid leaking upstream provider bodies, credentials, or user text.
        logger.error("request_failed error_type=%s", type(error).__name__)
        return JSONResponse(
            {"detail": "El servicio no está disponible. Puedes reintentar."}, status_code=503
        )

    @application.get("/api/health/live")
    async def live():
        return {"status": "ok"}

    @application.get("/api/health/ready")
    async def ready(request: Request):
        current = request.app.state.service
        await current.database.check()
        allowed = ["reviewed", "demo"] if settings.demo_sources_enabled else ["reviewed"]
        async with current.database.sessions() as session:
            count = await session.scalar(
                select(func.count()).select_from(Document).where(Document.trust_level.in_(allowed))
            )
        if current.graph is None:
            raise ServiceError(503, "Configura OPENAI_API_KEY para activar el modelo.")
        if not count:
            raise ServiceError(503, "Todavía no hay documentos habilitados en el índice.")
        return {
            "status": "ready",
            "documents": count,
            "demo_sources": settings.demo_sources_enabled,
        }

    @application.post("/api/conversations", status_code=201)
    async def create(body: ConversationCreate, request: Request, response: Response):
        if not body.consent:
            raise ServiceError(400, "Se requiere aceptar el procesamiento de la conversación.")
        current = request.app.state.service
        address = request.client.host if request.client else "unknown"
        await current.rate_limit("create:" + owner_hash(address))
        token = request.cookies.get(COOKIE, "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            token = secrets.token_urlsafe(32)
        identifier = await current.create(owner_hash(token))
        response.set_cookie(
            COOKIE,
            token,
            httponly=True,
            secure=settings.environment == "production",
            samesite="strict",
            max_age=settings.session_ttl_days * 86400,
            path="/api",
        )
        return {"conversation_id": identifier}

    @application.get("/api/conversations/{conversation_id}", response_model=list[MessageView])
    async def history(conversation_id: UUID, request: Request):
        return await request.app.state.service.history(conversation_id, get_owner(request))

    @application.delete("/api/conversations/{conversation_id}", status_code=204)
    async def remove(conversation_id: UUID, request: Request):
        await request.app.state.service.remove(conversation_id, get_owner(request))
        return Response(status_code=204)

    @application.post("/api/chat", response_model=ChatResponse)
    async def chat(body: ChatRequest, request: Request):
        owner = get_owner(request)
        current = request.app.state.service
        await current.rate_limit("chat:" + owner)
        return await current.respond(body, owner)

    return application


app = create_app()
