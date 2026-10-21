"""
The checks a change to a row starts with: that it exists, and that a slug
or name it takes is free. They raise errors that say what is wrong in
words, not HTTP, so that the services can be called from more than the
routes: main.py answers NotFoundError with 404 and ConflictError with 409.
"""

from typing import Any, TypeVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

Row = TypeVar("Row")


class NotFoundError(LookupError):
    """There is no such row. The message is fit to show the caller."""


class ConflictError(ValueError):
    """The change would clash with a row that exists. The message is fit to
    show the caller."""


async def get_or_raise(db: AsyncSession, model: type[Row], id: Any, what: str) -> Row:
    """The row with this primary key; NotFoundError "<what> not found" if
    there is none."""
    row = await db.get(model, id)
    if row is None:
        raise NotFoundError(f"{what} not found")
    return row


async def ensure_unique(
    db: AsyncSession,
    column: InstrumentedAttribute[Any],
    value: Any,
    what: str,
    *,
    exclude_id: UUID | None = None,
) -> None:
    """ConflictError if a row other than `exclude_id` (the one being
    updated) already has `value` in `column`: a slug or a name."""
    query = select(column).where(column == value)
    if exclude_id is not None:
        query = query.where(column.class_.id != exclude_id)
    if await db.scalar(query.limit(1)) is not None:
        raise ConflictError(f"{what} with {column.key} '{value}' already exists")
