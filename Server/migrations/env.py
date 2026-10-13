"""Adaptador de Alembic para ejecutar migraciones con el motor asíncrono del proyecto."""

import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.models import Base


def run_migrations(connection):
    """Configura metadatos y ejecuta las revisiones dentro de una transacción de Alembic."""
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


async def online():
    """Abre una conexión sin pool, ejecuta migraciones síncronas mediante run_sync y cierra."""
    engine = create_async_engine(get_settings().database_url, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=get_settings().database_url, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(online())
