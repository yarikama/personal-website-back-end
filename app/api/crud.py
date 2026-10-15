"""The checks the admin's create, update and delete routes share."""

from typing import Any, TypeVar
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

Row = TypeVar("Row")


async def get_or_404(db: AsyncSession, model: type[Row], id: Any, what: str) -> Row:
    """The row with this primary key; 404 "<what> not found" if there is none."""
    row = await db.get(model, id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"{what} not found")
    return row


async def ensure_unique(
    db: AsyncSession,
    column: InstrumentedAttribute[Any],
    value: Any,
    what: str,
    *,
    exclude_id: UUID | None = None,
) -> None:
    """409 if a row other than `exclude_id` (the one being updated) already
    has `value` in `column`: a slug or a name."""
    query = select(column).where(column == value)
    if exclude_id is not None:
        query = query.where(column.class_.id != exclude_id)
    if await db.scalar(query.limit(1)) is not None:
        raise HTTPException(
            status_code=409,
            detail=f"{what} with {column.key} '{value}' already exists",
        )
