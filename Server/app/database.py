"""Conexiones asíncronas a PostgreSQL y comprobación de compatibilidad del corpus."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.models import CorpusConfig


class Database:
    """Agrupa el motor SQLAlchemy y la fábrica de sesiones usados por los servicios."""

    def __init__(self, settings: Settings):
        """Prepara un pool de 10 conexiones y hasta 10 adicionales por proceso.

        No ejecuta migraciones ni consulta la base al construir el objeto.
        expire_on_commit=False permite leer objetos cargados después del commit.
        """
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
        """Comprueba la firma guardada en corpus_config frente a la configuración.

        Una tabla ausente o una conexión fallida propagan el error de PostgreSQL;
        una firma diferente produce RuntimeError. No comprueba el acceso a OpenAI.
        """
        async with self.sessions() as session:
            signature = await session.scalar(select(CorpusConfig.embedding_signature))
            if signature != self.settings.embedding_signature:
                raise RuntimeError(
                    "Corpus incompatible; run migrations or reindex into a new database."
                )

    async def close(self) -> None:
        """Libera las conexiones del pool al apagar la API o terminar una herramienta."""
        await self.engine.dispose()
