from typing import Any

from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession


async def count(db: AsyncSession, query: Select[Any]) -> int:
    """How many rows `query` returns: what Query.count() did, for a select().
    Its ORDER BY is dropped, as the order cannot change the count (and its
    loader options, such as joinedload, do not reach a subquery)."""
    subquery = query.order_by(None).subquery()
    return await db.scalar(select(func.count()).select_from(subquery)) or 0


def offset_pagination(offset: int, limit: int, total: int) -> dict[str, Any]:
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "hasMore": offset + limit < total,
    }


async def paginate(
    db: AsyncSession,
    query: Select[Any],
    schema: type[BaseModel],
    offset: int,
    limit: int,
) -> dict[str, Any]:
    """One page of `query`, in its own order, each row as `schema`: the
    {"data", "pagination"} the list endpoints answer with."""
    total = await count(db, query)
    rows = await db.scalars(query.offset(offset).limit(limit))
    return {
        "data": [schema.model_validate(row) for row in rows],
        "pagination": offset_pagination(offset, limit, total),
    }
