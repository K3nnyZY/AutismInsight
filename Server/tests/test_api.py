from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.errors import ServiceError
from app.main import COOKIE, create_app
from app.schemas import ChatResponse


class MemoryService:
    """Only the HTTP boundary is tested here; SQL is exercised in integration tests."""

    def __init__(self):
        self.owners = {}
        self.calls = []

    async def rate_limit(self, key):
        pass

    async def create(self, owner):
        identifier = uuid4()
        self.owners[identifier] = owner
        return identifier

    async def history(self, identifier, owner):
        if self.owners.get(identifier) != owner:
            raise ServiceError(404, "La conversación no está disponible.")
        return []

    async def respond(self, body, owner):
        await self.history(body.conversation_id, owner)
        self.calls.append(body)
        return ChatResponse(
            message_id=uuid4(),
            request_id=body.request_id,
            answer="Hola",
            sources=[],
            evidence_status="not_applicable",
        )

    async def remove(self, identifier, owner):
        await self.history(identifier, owner)
        del self.owners[identifier]


@pytest.fixture
def client():
    with TestClient(create_app(Settings(_env_file=None, environment="test"), MemoryService())) as c:
        yield c


def test_consent_and_cookie_security(client):
    assert client.post("/api/conversations", json={"consent": False}).status_code == 400
    response = client.post("/api/conversations", json={"consent": True})
    assert response.status_code == 201
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert "Path=/api" in cookie
    assert response.headers["cache-control"] == "no-store"


def test_owner_cookie_is_required_and_isolated(client):
    identifier = client.post("/api/conversations", json={"consent": True}).json()["conversation_id"]
    assert client.get(f"/api/conversations/{identifier}").status_code == 200
    client.cookies.clear()
    assert client.get(f"/api/conversations/{identifier}").status_code == 401
    client.post("/api/conversations", json={"consent": True})
    assert client.get(f"/api/conversations/{identifier}").status_code == 404


def test_delete_revokes_access(client):
    identifier = client.post("/api/conversations", json={"consent": True}).json()["conversation_id"]
    assert client.delete(f"/api/conversations/{identifier}").status_code == 204
    assert client.get(f"/api/conversations/{identifier}").status_code == 404


@pytest.mark.parametrize("message", ["", "   ", "x" * 4001])
def test_invalid_questions_rejected(client, message):
    identifier = client.post("/api/conversations", json={"consent": True}).json()["conversation_id"]
    response = client.post(
        "/api/chat",
        json={
            "conversation_id": identifier,
            "request_id": str(uuid4()),
            "message": message,
        },
    )
    assert response.status_code == 422


def test_untrusted_origin_rejected(client):
    response = client.post(
        "/api/conversations", json={"consent": True}, headers={"Origin": "https://untrusted.test"}
    )
    assert response.status_code == 403


def test_production_requires_origin_and_secure_cookie():
    app = create_app(
        Settings(_env_file=None, environment="production", cors_origins=["https://insight.test"]),
        MemoryService(),
    )
    with TestClient(app, base_url="https://insight.test") as client:
        assert client.post("/api/conversations", json={"consent": True}).status_code == 403
        response = client.post(
            "/api/conversations", json={"consent": True}, headers={"Origin": "https://insight.test"}
        )
        assert "Secure" in response.headers["set-cookie"]
        assert COOKIE in client.cookies


def test_body_size_and_unknown_fields(client):
    assert client.post("/api/conversations", content="x" * 17000).status_code == 413
    assert (
        client.post("/api/conversations", json={"consent": True, "admin": True}).status_code == 422
    )


def test_chunked_body_limit(client):
    def body():
        for _ in range(20):
            yield b"x" * 1000

    assert client.post("/api/conversations", content=body()).status_code == 413


def test_production_never_enables_demo_sources():
    settings = Settings(_env_file=None, environment="production", allow_demo_sources=True)
    assert not settings.demo_sources_enabled
