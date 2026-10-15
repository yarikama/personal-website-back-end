from collections.abc import AsyncGenerator
from typing import Annotated

from db.session import SessionLocal
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """A database session for the request, closed after the response."""
    async with SessionLocal() as db:
        yield db


# A route's session: `db: DB`. Tests still override get_db.
DB = Annotated[AsyncSession, Depends(get_db)]
