from uuid import UUID

from api.crud import ensure_unique, get_or_404
from api.dependencies import CurrentAdmin
from core.paginator import paginate
from db.dependency import DB
from db.models.lab_notes import LabNote
from fastapi import APIRouter, HTTPException, Path, Query
from schemas.lab_notes import (
    LabNoteCreate,
    LabNoteListResponse,
    LabNoteResponse,
    LabNoteUpdate,
)
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


async def list_notes(
    db: AsyncSession, query: Select, tag: str | None, limit: int, offset: int
) -> dict:
    """A page of `query`'s notes, newest first, optionally only one tag's."""
    if tag:
        query = query.where(LabNote.tags.contains([tag]))
    query = query.order_by(LabNote.date.desc())
    return await paginate(db, query, LabNoteListResponse, offset, limit)


# ============================================================================
# Admin Routes (require authentication)
# ============================================================================


@router.get("/admin/lab-notes")
async def get_all_lab_notes_admin(
    _admin: CurrentAdmin,
    db: DB,
    tag: str | None = Query(None),
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Admin endpoint: Get all lab notes including unpublished."""
    return await list_notes(db, select(LabNote), tag, limit, offset)


@router.get("/admin/lab-notes/{id}")
async def get_lab_note_by_id(id: UUID, _admin: CurrentAdmin, db: DB):
    """Admin endpoint: Get a single lab note by ID (including unpublished)."""
    lab_note = await get_or_404(db, LabNote, id, "Lab note")
    return {"data": LabNoteResponse.model_validate(lab_note)}


@router.post("/admin/lab-notes", status_code=201)
async def create_lab_note(lab_note: LabNoteCreate, _admin: CurrentAdmin, db: DB):
    """Admin endpoint: Create a new lab note."""
    await ensure_unique(db, LabNote.slug, lab_note.slug, "Lab note")

    db_lab_note = LabNote(**lab_note.model_dump())
    db.add(db_lab_note)
    await db.commit()
    await db.refresh(db_lab_note)
    return {"data": LabNoteResponse.model_validate(db_lab_note)}


@router.put("/admin/lab-notes/{id}")
async def update_lab_note(
    id: UUID, lab_note: LabNoteUpdate, _admin: CurrentAdmin, db: DB
):
    """Admin endpoint: Update a lab note."""
    db_lab_note = await get_or_404(db, LabNote, id, "Lab note")

    update_data = lab_note.model_dump(exclude_unset=True)
    if "slug" in update_data:
        await ensure_unique(
            db, LabNote.slug, update_data["slug"], "Lab note", exclude_id=id
        )

    for field, value in update_data.items():
        setattr(db_lab_note, field, value)

    await db.commit()
    # Also reads updated_at back, which the database set (onupdate=now()).
    await db.refresh(db_lab_note)
    return {"data": LabNoteResponse.model_validate(db_lab_note)}


@router.delete("/admin/lab-notes/{id}", status_code=204)
async def delete_lab_note(id: UUID, _admin: CurrentAdmin, db: DB):
    """Admin endpoint: Delete a lab note."""
    await db.delete(await get_or_404(db, LabNote, id, "Lab note"))
    await db.commit()


# ============================================================================
# Public Routes (no authentication required)
# ============================================================================


@router.get("/lab-notes")
async def get_lab_notes(
    db: DB,
    tag: str | None = Query(None),
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Public endpoint: Get only published lab notes."""
    published = select(LabNote).where(LabNote.published.is_(True))
    return await list_notes(db, published, tag, limit, offset)


@router.get("/lab-notes/tags")
async def get_lab_notes_tags(db: DB):
    """Public endpoint: Get all tags used in published lab notes."""
    result = (
        select(func.unnest(LabNote.tags).label("tag"))
        .where(LabNote.published.is_(True))
        .subquery()
    )

    tags = await db.execute(
        select(result.c.tag, func.count(result.c.tag).label("count"))
        .group_by(result.c.tag)
        .order_by(func.count(result.c.tag).desc())
    )

    return {"data": [{"tag": tag, "count": n} for tag, n in tags]}


@router.get("/lab-notes/{slug}")
async def get_lab_note(db: DB, slug: str = Path(..., min_length=1, max_length=255)):
    """Public endpoint: Get a single published lab note by slug."""
    lab_note = await db.scalar(
        select(LabNote).where(LabNote.slug == slug, LabNote.published.is_(True))
    )
    if not lab_note:
        raise HTTPException(status_code=404, detail="Lab note not found")
    return {"data": LabNoteResponse.model_validate(lab_note)}
