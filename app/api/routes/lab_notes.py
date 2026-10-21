from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query

from api.dependencies import CurrentAdmin
from db.dependency import DB
from schemas.lab_notes import LabNoteCreate, LabNoteResponse, LabNoteUpdate
from services.content import lab_notes

router = APIRouter()

# ============================================================================
# Admin Routes (require authentication)
# ============================================================================


@router.get("/admin/lab-notes")
async def get_all_lab_notes_admin(
    _admin: CurrentAdmin,
    db: DB,
    tag: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """Admin endpoint: Get all lab notes including unpublished."""
    return await lab_notes.page(
        db, published_only=False, tag=tag, limit=limit, offset=offset
    )


@router.get("/admin/lab-notes/{id}")
async def get_lab_note_by_id(id: UUID, _admin: CurrentAdmin, db: DB):
    """Admin endpoint: Get a single lab note by ID (including unpublished)."""
    return {"data": LabNoteResponse.model_validate(await lab_notes.get(db, id))}


@router.post("/admin/lab-notes", status_code=201)
async def create_lab_note(lab_note: LabNoteCreate, _admin: CurrentAdmin, db: DB):
    """Admin endpoint: Create a new lab note."""
    created = await lab_notes.create(db, lab_note)
    return {"data": LabNoteResponse.model_validate(created)}


@router.put("/admin/lab-notes/{id}")
async def update_lab_note(
    id: UUID, lab_note: LabNoteUpdate, _admin: CurrentAdmin, db: DB
):
    """Admin endpoint: Update a lab note."""
    updated = await lab_notes.update(db, id, lab_note)
    return {"data": LabNoteResponse.model_validate(updated)}


@router.delete("/admin/lab-notes/{id}", status_code=204)
async def delete_lab_note(id: UUID, _admin: CurrentAdmin, db: DB):
    """Admin endpoint: Delete a lab note."""
    await lab_notes.delete(db, id)


# ============================================================================
# Public Routes (no authentication required)
# ============================================================================


@router.get("/lab-notes")
async def get_lab_notes(
    db: DB,
    tag: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """Public endpoint: Get only published lab notes."""
    return await lab_notes.page(
        db, published_only=True, tag=tag, limit=limit, offset=offset
    )


@router.get("/lab-notes/tags")
async def get_lab_notes_tags(db: DB):
    """Public endpoint: Get all tags used in published lab notes."""
    return {"data": await lab_notes.tags(db)}


@router.get("/lab-notes/{slug}")
async def get_lab_note(
    db: DB, slug: Annotated[str, Path(min_length=1, max_length=255)]
):
    """Public endpoint: Get a single published lab note by slug."""
    note = await lab_notes.get_published(db, slug)
    return {"data": LabNoteResponse.model_validate(note)}
