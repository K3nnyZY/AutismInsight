"""Run daily to enforce retention. No prompts or conversation content are printed."""

import asyncio

from sqlalchemy import delete, func, text

from app.config import get_settings
from app.database import Database
from app.models import Conversation


async def main():
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
