import json
import logging
import time

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from app.config import EMBEDDING_DIMENSIONS, Settings

logger = logging.getLogger(__name__)


def create_embeddings(settings: Settings, http_async_client=None):
    return OpenAIEmbeddings(
        model=settings.embedding_model,
        dimensions=EMBEDDING_DIMENSIONS,
        api_key=settings.openai_api_key.get_secret_value(),
        request_timeout=25,
        max_retries=1,
        chunk_size=32,
        http_async_client=http_async_client,
    )


class StructuredModels:
    """LangChain provider boundary. Prompts and source text never enter application logs."""

    def __init__(self, settings: Settings, http_async_client=None):
        options = {
            "api_key": settings.openai_api_key.get_secret_value(),
            "timeout": 30,
            "max_retries": 1,
            "max_completion_tokens": 4096,
            "http_async_client": http_async_client,
        }
        self.models = {
            "planner": ChatOpenAI(model=settings.planner_model, **options),
            "answer": ChatOpenAI(model=settings.chat_model, **options),
        }

    async def call(self, schema, system: str, payload: dict, role: str = "planner"):
        started = time.monotonic()
        result = (
            await self.models[role]
            .with_structured_output(schema, method="json_schema", strict=True, include_raw=True)
            .ainvoke(
                [
                    SystemMessage(content=system),
                    HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
                ]
            )
        )
        usage = result["raw"].usage_metadata or {}
        logger.info(
            "model_call schema=%s duration_ms=%d input_tokens=%s output_tokens=%s",
            schema.__name__,
            int((time.monotonic() - started) * 1000),
            usage.get("input_tokens"),
            usage.get("output_tokens"),
        )
        if result["parsed"] is None or result.get("parsing_error"):
            raise RuntimeError("The model did not return a valid structured result.")
        return result["parsed"]
