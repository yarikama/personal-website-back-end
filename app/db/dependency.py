from collections.abc import AsyncGenerator, Generator

from db.session import AsyncSessionLocal, SessionLocal
from sqlalchemy.ext.asyncio import AsyncSession


def get_db() -> Generator:
    """
    Dependency that provides a database session.
    Automatically closes the session when done.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """get_db for async routes: the session's queries are awaited."""
    async with AsyncSessionLocal() as db:
        yield db
