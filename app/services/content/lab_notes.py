from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.paginator import paginate
from db.models.lab_notes import LabNote
from db.rows import NotFoundError, ensure_unique, get_or_raise
from schemas.lab_notes import LabNoteCreate, LabNoteListResponse, LabNoteUpdate

WHAT = "Lab note"


async def page(
    db: AsyncSession,
    *,
    published_only: bool,
    tag: str | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """A page of notes, newest first, optionally only one tag's; drafts too
    unless `published_only`."""
    query = select(LabNote)
    if published_only:
        query = query.where(LabNote.published.is_(True))
    if tag:
        query = query.where(LabNote.tags.contains([tag]))
    query = query.order_by(LabNote.date.desc())
    return await paginate(db, query, LabNoteListResponse, offset, limit)


async def get(db: AsyncSession, id: UUID) -> LabNote:
    """The note, draft or not."""
    return await get_or_raise(db, LabNote, id, WHAT)


async def get_published(db: AsyncSession, slug: str) -> LabNote:
    """The published note with this slug. A draft is not found: its slug
    must not tell a visitor that it exists."""
    note = await db.scalar(
        select(LabNote).where(LabNote.slug == slug, LabNote.published.is_(True))
    )
    if note is None:
        raise NotFoundError(f"{WHAT} not found")
    return note


async def tags(db: AsyncSession) -> list[dict[str, Any]]:
    """Every tag of a published note with how many use it, most used first."""
    tag = (
        select(func.unnest(LabNote.tags).label("tag"))
        .where(LabNote.published.is_(True))
        .subquery()
    )
    rows = await db.execute(
        select(tag.c.tag, func.count(tag.c.tag).label("count"))
        .group_by(tag.c.tag)
        .order_by(func.count(tag.c.tag).desc())
    )
    return [{"tag": name, "count": n} for name, n in rows]


async def create(db: AsyncSession, fields: LabNoteCreate) -> LabNote:
    await ensure_unique(db, LabNote.slug, fields.slug, WHAT)
    note = LabNote(**fields.model_dump())
    db.add(note)
    await db.commit()
    await db.refresh(note)
    return note


async def update(db: AsyncSession, id: UUID, fields: LabNoteUpdate) -> LabNote:
    """Sets the fields `fields` was given, and no others."""
    note = await get(db, id)
    changes = fields.model_dump(exclude_unset=True)
    if "slug" in changes:
        await ensure_unique(db, LabNote.slug, changes["slug"], WHAT, exclude_id=id)
    for field, value in changes.items():
        setattr(note, field, value)
    await db.commit()
    # Also reads updated_at back, which the database set (onupdate=now()).
    await db.refresh(note)
    return note


async def delete(db: AsyncSession, id: UUID) -> None:
    await db.delete(await get(db, id))
    await db.commit()
