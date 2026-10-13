"""Limpieza manual o programada de sesiones vencidas y contadores antiguos.

Ejecutar diariamente: `python -m app.maintenance`. La API no programa esta tarea.
"""

import asyncio

from sqlalchemy import delete, func, text

from app.config import get_settings
from app.database import Database
from app.models import Conversation


async def main():
    """Borra conversaciones vencidas y contadores de más de 120 minutos.

    La base elimina los mensajes en cascada. Imprime solo el número
    de conversaciones eliminadas y libera las conexiones al terminar.
    """
    database = Database(get_settings())
    try:
        async with database.sessions.begin() as session:
            result = await session.execute(
                delete(Conversation).where(Conversation.expires_at < func.now())
            )
            await session.execute(
                text("""
                DELETE FROM rate_buckets
                WHERE minute < floor(extract(epoch from clock_timestamp()) / 60)::bigint - 120
            """)
            )
            print(f"Expired conversations removed: {result.rowcount}")
    finally:
        await database.close()


if __name__ == "__main__":
    asyncio.run(main())
