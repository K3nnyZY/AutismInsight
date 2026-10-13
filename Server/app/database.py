from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.models import CorpusConfig


class Database:
    def __init__(self, settings: Settings):
        self.engine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=10,
            pool_timeout=10,
            connect_args={"connect_timeout": 5},
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.settings = settings

    async def check(self) -> None:
        async with self.sessions() as session:
            signature = await session.scalar(select(CorpusConfig.embedding_signature))
            if signature != self.settings.embedding_signature:
                raise RuntimeError(
                    "Corpus incompatible; run migrations or reindex into a new database."
                )

    async def close(self) -> None:
        await self.engine.dispose()
