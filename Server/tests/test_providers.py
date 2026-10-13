import json
import logging

import httpx
import pytest

from app.config import EMBEDDING_DIMENSIONS, Settings
from app.graph import Plan
from app.providers import StructuredModels, create_embeddings


async def test_langchain_structured_output_contract_and_private_logging(caplog):
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert request.url.path.endswith("/chat/completions")
        return httpx.Response(
            200,
            json={
                "id": "chat-test",
                "object": "chat.completion",
                "created": 0,
                "model": "gpt-5.4-mini",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": '{"route":"knowledge","query":"autismo"}',
                        },
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18},
            },
        )

    settings = Settings(_env_file=None, openai_api_key="test-secret")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        models = StructuredModels(settings, http_async_client=client)
        with caplog.at_level(logging.INFO, logger="app.providers"):
            result = await models.call(Plan, "System boundary", {"question": "private-user-text"})
    assert result.route == "knowledge"
    assert calls[0]["response_format"]["json_schema"]["strict"] is True
    assert "private-user-text" not in caplog.text and "test-secret" not in caplog.text
    assert "input_tokens=12" in caplog.text


async def test_provider_refusal_is_not_parsed_as_an_answer():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "id": "chat-test",
                "object": "chat.completion",
                "created": 0,
                "model": "gpt-5.4-mini",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "refusal": "Cannot answer",
                        },
                    }
                ],
            },
        )

    settings = Settings(_env_file=None, openai_api_key="test-secret")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        models = StructuredModels(settings, http_async_client=client)
        with pytest.raises(RuntimeError, match="valid structured"):
            await models.call(Plan, "System", {"question": "test"})


async def test_embedding_contract_uses_correct_model_and_dimensions():
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        return httpx.Response(
            200,
            json={
                "object": "list",
                "model": "text-embedding-3-large",
                "data": [
                    {
                        "object": "embedding",
                        "index": 0,
                        "embedding": [1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1),
                    }
                ],
                "usage": {"prompt_tokens": 4, "total_tokens": 4},
            },
        )

    settings = Settings(_env_file=None, openai_api_key="test-secret")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        embeddings = create_embeddings(settings, http_async_client=client)
        assert len(await embeddings.aembed_query("comunicación")) == EMBEDDING_DIMENSIONS
    assert calls[0]["model"] == "text-embedding-3-large"
    assert calls[0]["dimensions"] == EMBEDDING_DIMENSIONS
