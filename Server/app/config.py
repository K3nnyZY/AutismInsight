from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
EMBEDDING_DIMENSIONS = 1536


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )
    environment: Literal["development", "production", "test"] = "development"
    database_url: str = "postgresql+psycopg://autism:local-dev-only@localhost:5432/autism_insight"
    openai_api_key: SecretStr = SecretStr("")
    chat_model: str = "gpt-5.4-mini"
    planner_model: str = "gpt-5.4-mini"
    embedding_model: str = "text-embedding-3-large"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    allow_demo_sources: bool = False
    chat_timeout_seconds: int = Field(default=90, ge=10, le=180)
    session_ttl_days: int = Field(default=30, ge=1, le=90)
    rate_limit_per_minute: int = Field(default=12, ge=1, le=120)
    retrieval_candidates: int = Field(default=16, ge=4, le=32)
    retrieval_top_k: int = Field(default=6, ge=1, le=8)
    history_turns: int = Field(default=8, ge=1, le=12)
    max_history_chars: int = Field(default=18000, ge=1000, le=40000)

    @property
    def embedding_signature(self) -> str:
        return f"{self.embedding_model}:{EMBEDDING_DIMENSIONS}"

    @property
    def demo_sources_enabled(self) -> bool:
        return self.environment != "production" and self.allow_demo_sources


@lru_cache
def get_settings() -> Settings:
    return Settings()
