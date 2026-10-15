from collections.abc import AsyncGenerator

from db.session import SessionLocal
from sqlalchemy.ext.asyncio import AsyncSession


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """A database session for the request, closed after the response."""
    async with SessionLocal() as db:
        yield db
