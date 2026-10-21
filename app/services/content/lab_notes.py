import re
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.paginator import paginate
from db.models.lab_notes import LabNote
from db.rows import NotFoundError, ensure_unique, get_or_raise
from schemas.lab_notes import LabNoteCreate, LabNoteListResponse, LabNoteUpdate

WHAT = "Lab note"

# Reading speed for read_time: words of English, and characters of Chinese
# or Japanese, which have no spaces to count words by. Of the seven notes
# whose read time was set by hand, 175 words a minute (rounded) gives five
# the same time and the other two one minute less.
WORDS_PER_MINUTE = 175
CJK_CHARACTERS_PER_MINUTE = 350
WORD = re.compile(r"[A-Za-z0-9]+(?:['’.-][A-Za-z0-9]+)*")
CJK_CHARACTER = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]")


def read_time(content: str) -> str:
    """How long `content` takes to read, as the notes show it: "6 min read"."""
    minutes = (
        len(WORD.findall(content)) / WORDS_PER_MINUTE
        + len(CJK_CHARACTER.findall(content)) / CJK_CHARACTERS_PER_MINUTE
    )
    return f"{max(1, round(minutes))} min read"


def contains(text: str) -> str:
    """An ILIKE pattern for `text` anywhere, its own % and _ taken literally."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def page(
    db: AsyncSession,
    *,
    published: bool | None,
    tag: str | None,
    limit: int,
    offset: int,
    search: str | None = None,
) -> dict[str, Any]:
    """A page of notes, newest first: only published ones or only drafts, or
    both if `published` is None; optionally only one tag's, or those with
    `search` in their title, excerpt or body (ignoring case)."""
    query = select(LabNote)
    if published is not None:
        query = query.where(LabNote.published.is_(published))
    if tag:
        query = query.where(LabNote.tags.contains([tag]))
    if search:
        pattern = contains(search)
        query = query.where(
            or_(
                LabNote.title.ilike(pattern, escape="\\"),
                LabNote.excerpt.ilike(pattern, escape="\\"),
                LabNote.content.ilike(pattern, escape="\\"),
            )
        )
    query = query.order_by(LabNote.date.desc())
    return await paginate(db, query, LabNoteListResponse, offset, limit)


async def get(db: AsyncSession, id: UUID) -> LabNote:
    """The note, draft or not."""
    return await get_or_raise(db, LabNote, id, WHAT)


async def get_by_slug(db: AsyncSession, slug: str) -> LabNote:
    """The note with this slug, draft or not."""
    note = await db.scalar(select(LabNote).where(LabNote.slug == slug))
    if note is None:
        raise NotFoundError(f"{WHAT} not found")
    return note


async def get_published(db: AsyncSession, slug: str) -> LabNote:
    """The published note with this slug. A draft is not found: its slug
    must not tell a visitor that it exists."""
    note = await db.scalar(
        select(LabNote).where(LabNote.slug == slug, LabNote.published.is_(True))
    )
    if note is None:
        raise NotFoundError(f"{WHAT} not found")
    return note


async def tags(
    db: AsyncSession, *, published_only: bool = True
) -> list[dict[str, Any]]:
    """Every tag of a published note (or of any note, drafts too) with how
    many use it, most used first."""
    tag = select(func.unnest(LabNote.tags).label("tag"))
    if published_only:
        tag = tag.where(LabNote.published.is_(True))
    tag = tag.subquery()
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
