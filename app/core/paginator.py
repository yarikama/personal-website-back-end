from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession


async def count(db: AsyncSession, query: Select) -> int:
    """How many rows `query` returns: what Query.count() did, for a select()."""
    return await db.scalar(select(func.count()).select_from(query.subquery())) or 0


def offset_pagination(offset: int, limit: int, total: int) -> dict:
    """Return offset-based pagination metadata.

    Args:
        offset: Number of items to skip
        limit: Maximum number of items to return
        total: Total number of items available

    Returns:
        dict with total, limit, offset, hasMore
    """
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "hasMore": offset + limit < total,
    }
