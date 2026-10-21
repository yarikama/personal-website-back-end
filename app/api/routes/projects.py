from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query

from api.dependencies import CurrentAdmin
from db.dependency import DB
from schemas.projects import ProjectCreate, ProjectResponse, ProjectUpdate
from services.content import projects

router = APIRouter()

# ============================================================================
# Admin Routes (require authentication)
# ============================================================================


@router.get("/admin/projects")
async def get_all_projects_admin(
    _admin: CurrentAdmin,
    db: DB,
    category_id: Annotated[
        UUID | None, Query(description="Filter by category UUID")
    ] = None,
    featured: Annotated[bool | None, Query()] = None,
    tag: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """Admin endpoint: Get all projects including unpublished."""
    return await projects.page(
        db,
        published=None,
        category_id=category_id,
        featured=featured,
        tag=tag,
        limit=limit,
        offset=offset,
    )


@router.post("/admin/projects", status_code=201)
async def create_project(project_in: ProjectCreate, _admin: CurrentAdmin, db: DB):
    """Admin endpoint: Create a new project."""
    created = await projects.create(db, project_in)
    return {"data": ProjectResponse.model_validate(created)}


@router.put("/admin/projects/{id}")
async def update_project(
    id: UUID, project_in: ProjectUpdate, _admin: CurrentAdmin, db: DB
):
    """Admin endpoint: Update a project."""
    updated = await projects.update(db, id, project_in)
    return {"data": ProjectResponse.model_validate(updated)}


@router.delete("/admin/projects/{id}", status_code=204)
async def delete_project(id: UUID, _admin: CurrentAdmin, db: DB):
    """Admin endpoint: Delete a project."""
    await projects.delete(db, id)


# ============================================================================
# Public Routes (no authentication required)
# ============================================================================


@router.get("/projects")
async def get_projects(
    db: DB,
    category_id: Annotated[
        UUID | None, Query(description="Filter by category UUID")
    ] = None,
    featured: Annotated[bool | None, Query()] = None,
    tag: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """Public endpoint: Get only published projects."""
    return await projects.page(
        db,
        published=True,
        category_id=category_id,
        featured=featured,
        tag=tag,
        limit=limit,
        offset=offset,
    )


@router.get("/projects/{slug}")
async def get_project(db: DB, slug: Annotated[str, Path(min_length=1, max_length=255)]):
    """Public endpoint: Get a single published project by slug."""
    project = await projects.get_published(db, slug)
    return {"data": ProjectResponse.model_validate(project)}
