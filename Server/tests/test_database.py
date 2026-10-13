import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

from app.config import EMBEDDING_DIMENSIONS, Settings
from app.database import Database
from app.errors import ServiceError
from app.ingest import ingest
from app.models import Chunk, Conversation, Document, Message
from app.retrieval import HybridRetriever
from app.schemas import ChatRequest
from app.service import ConversationService

pytestmark = pytest.mark.integration
DEMO_PDF = Path(__file__).resolve().parents[1] / "app" / "Asperger.pdf"


class FakeEmbeddings:
    def __init__(self):
        self.calls = 0

    async def aembed_documents(self, texts):
        self.calls += 1
        return [[1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1) for _ in texts]

    async def aembed_query(self, query):
        return [1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1)


class FakeGraph:
    def __init__(self):
        self.calls = []

    async def ainvoke(self, state, config):
        self.calls.append(state)
        return {"answer": "Respuesta de prueba", "sources": [], "evidence_status": "not_applicable"}


@pytest.fixture
async def database():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to an isolated, migrated pgvector database.")
    if not url.split("?")[0].endswith("_test"):
        pytest.fail("Integration tests require a database name ending in _test.")
    settings = Settings(
        _env_file=None, database_url=url, environment="test", allow_demo_sources=True
    )
    database = Database(settings)
    await database.check()
    async with database.engine.begin() as connection:
        await connection.execute(
            text("TRUNCATE messages, conversations, chunks, documents, rate_buckets CASCADE")
        )
    try:
        yield database
    finally:
        await database.close()


def request(identifier, **kwargs):
    return ChatRequest(conversation_id=identifier, request_id=uuid4(), message="Hola", **kwargs)


async def test_schema_indexes_and_model_signature(database):
    async with database.sessions() as session:
        indexes = list(
            await session.scalars(
                text("SELECT indexname FROM pg_indexes WHERE tablename = 'chunks'")
            )
        )
    assert {"chunks_hnsw_idx", "chunks_fts_idx", "chunks_document_idx"}.issubset(indexes)
    database.settings.embedding_model = "different-model"
    with pytest.raises(RuntimeError, match="incompatible"):
        await database.check()


async def test_idempotency_isolation_and_delete_cascade(database):
    graph = FakeGraph()
    service = ConversationService(database, graph, database.settings)
    identifier = await service.create("owner-a")
    body = request(identifier)
    first = await service.respond(body, "owner-a")
    second = await service.respond(body, "owner-a")
    assert first == second and len(graph.calls) == 1
    with pytest.raises(ServiceError) as conflict:
        await service.respond(body.model_copy(update={"message": "Otra pregunta"}), "owner-a")
    assert conflict.value.status == 409
    with pytest.raises(ServiceError) as forbidden:
        await service.history(identifier, "owner-b")
    assert forbidden.value.status == 404
    await service.respond(request(identifier), "owner-a")
    assert [m["role"] for m in graph.calls[1]["history"]] == ["user", "assistant"]
    await service.remove(identifier, "owner-a")
    async with database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Message)) == 0


async def test_lease_serializes_workers_and_delete_blocks_late_persistence(database):
    started, release = asyncio.Event(), asyncio.Event()

    class DelayedGraph(FakeGraph):
        async def ainvoke(self, state, config):
            started.set()
            await release.wait()
            return await super().ainvoke(state, config)

    service = ConversationService(database, DelayedGraph(), database.settings)
    other = ConversationService(database, FakeGraph(), database.settings)
    identifier = await service.create("owner")
    pending = asyncio.create_task(service.respond(request(identifier), "owner"))
    await asyncio.wait_for(started.wait(), 5)
    try:
        with pytest.raises(ServiceError) as conflict:
            await other.respond(request(identifier), "owner")
        assert conflict.value.status == 409
        await other.remove(identifier, "owner")
        release.set()
        with pytest.raises(ServiceError) as deleted:
            await pending
        assert deleted.value.status == 404
        async with database.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(Message)) == 0
    finally:
        release.set()
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


async def test_failure_releases_lease_and_does_not_save_partial_messages(database):
    class FailedGraph:
        async def ainvoke(self, state, config):
            raise RuntimeError("provider failure")

    service = ConversationService(database, FailedGraph(), database.settings)
    identifier = await service.create("owner")
    body = request(identifier)
    with pytest.raises(RuntimeError):
        await service.respond(body, "owner")
    assert await service.history(identifier, "owner") == []
    service.graph = FakeGraph()
    assert (await service.respond(body, "owner")).answer == "Respuesta de prueba"


async def test_expired_conversation_is_inaccessible(database):
    async with database.sessions.begin() as session:
        identifier = uuid4()
        session.add(
            Conversation(
                id=identifier,
                owner_hash="owner",
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        )
    service = ConversationService(database, FakeGraph(), database.settings)
    with pytest.raises(ServiceError) as expired:
        await service.history(identifier, "owner")
    assert expired.value.status == 404


async def test_distributed_rate_limit(database):
    database.settings.rate_limit_per_minute = 2
    first = ConversationService(database, FakeGraph(), database.settings)
    second = ConversationService(database, FakeGraph(), database.settings)
    await first.rate_limit("test-key")
    await second.rate_limit("test-key")
    with pytest.raises(ServiceError) as limited:
        await first.rate_limit("test-key")
    assert limited.value.status == 429


async def test_ingest_deduplicates_replaces_and_preserves_existing_index_on_failure(
    database, tmp_path
):
    path = tmp_path / "source.md"
    path.write_text("Comunicación clara y directa.", encoding="utf-8")
    embeddings = FakeEmbeddings()
    args = (database, embeddings, path, "test-source", "Fuente de prueba", None, "demo")
    first = await ingest(*args)
    repeated = await ingest(*args)
    assert repeated["status"] == "unchanged" and embeddings.calls == 1
    path.write_text("Nuevo contenido sobre accesibilidad.", encoding="utf-8")
    updated = await ingest(*args)
    assert first["document_id"] == updated["document_id"]
    async with database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Document)) == 1
        assert "Nuevo" in await session.scalar(select(Chunk.content))

    class FailedEmbeddings(FakeEmbeddings):
        async def aembed_documents(self, texts):
            raise RuntimeError("provider failure")

    path.write_text("Replacement that fails.", encoding="utf-8")
    with pytest.raises(RuntimeError):
        await ingest(
            database, FailedEmbeddings(), path, "test-source", "Fuente de prueba", None, "demo"
        )
    async with database.sessions() as session:
        assert "Nuevo" in await session.scalar(select(Chunk.content))


async def test_pgvector_hybrid_search_and_demo_filter(database):
    embeddings = FakeEmbeddings()
    await ingest(database, embeddings, DEMO_PDF, "asperger-demo", "Asperger demo", None, "demo")
    retriever = HybridRetriever(database, embeddings, database.settings)
    result = await retriever.search("comunicación")
    assert result and result[0].page == 1
    assert result[0].trust_level == "demo"
    database.settings.allow_demo_sources = False
    assert await retriever.search("comunicación") == []
