from uuid import UUID

from api.dependencies import CurrentAdmin
from core.paginator import count, offset_pagination
from db.dependency import get_db
from db.models.category import Category
from db.models.projects import Project
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from schemas.projects import (
    ProjectCreate,
    ProjectReorderRequest,
    ProjectResponse,
    ProjectUpdate,
)
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

router = APIRouter()

# Display order: the `order` set through the admin reorder endpoint, then newest
# first. id breaks any remaining tie so offset pagination is stable.
PROJECT_DISPLAY_ORDER = (Project.order, Project.created_at.desc(), Project.id)

# ProjectResponse reads the category, which an async session cannot load on
# access (Project.category_rel is lazy="raise"): every query that returns
# projects loads it with them.
WITH_CATEGORY = joinedload(Project.category_rel)


async def reload(db: AsyncSession, id: UUID) -> Project | None:
    """The project as the database has it now, with its category, after a
    commit. The query fills in what the session has expired or never loaded
    (updated_at, set by the UPDATE; the category); populate_existing also
    overwrites what it did load, so a category read before a change of
    category_id cannot be returned stale."""
    return await db.scalar(
        select(Project)
        .options(WITH_CATEGORY)
        .where(Project.id == id)
        .execution_options(populate_existing=True)
    )


# ============================================================================
# Admin Routes (require authentication)
# ============================================================================


@router.get("/admin/projects")
async def get_all_projects_admin(
    _admin: CurrentAdmin,
    db: AsyncSession = Depends(get_db),
    category_id: UUID | None = Query(None, description="Filter by category UUID"),
    featured: bool | None = Query(None),
    tag: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Admin endpoint: Get all projects including unpublished."""
    query = select(Project)

    if category_id:
        query = query.where(Project.category_id == category_id)
    if featured is not None:
        query = query.where(Project.featured == featured)
    if tag:
        query = query.where(Project.tags.contains([tag]))

    total = await count(db, query)
    projects = await db.scalars(
        query.options(WITH_CATEGORY)
        .order_by(*PROJECT_DISPLAY_ORDER)
        .offset(offset)
        .limit(limit)
    )

    return {
        "data": [ProjectResponse.model_validate(p) for p in projects],
        "pagination": offset_pagination(offset, limit, total),
    }


@router.get("/admin/projects/{id}")
async def get_project_by_id(
    id: UUID,
    _admin: CurrentAdmin,
    db: AsyncSession = Depends(get_db),
):
    """Admin endpoint: Get a single project by ID (including unpublished)."""
    project = await db.scalar(
        select(Project).options(WITH_CATEGORY).where(Project.id == id)
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"data": ProjectResponse.model_validate(project)}


@router.post("/admin/projects", status_code=201)
async def create_project(
    project_in: ProjectCreate,
    _admin: CurrentAdmin,
    db: AsyncSession = Depends(get_db),
):
    """Admin endpoint: Create a new project."""
    # Check if slug already exists
    is_exists = await db.scalar(select(Project).where(Project.slug == project_in.slug))
    if is_exists:
        raise HTTPException(
            status_code=409,
            detail=f"Project with slug '{project_in.slug}' already exists",
        )

    # Validate category exists
    category = await db.get(Category, project_in.category_id)
    if not category:
        raise HTTPException(
            status_code=404,
            detail=f"Category with id '{project_in.category_id}' not found",
        )

    project = Project(**project_in.model_dump())
    db.add(project)
    await db.commit()

    return {"data": ProjectResponse.model_validate(await reload(db, project.id))}


@router.put("/admin/projects/{id}")
async def update_project(
    id: UUID,
    project_in: ProjectUpdate,
    _admin: CurrentAdmin,
    db: AsyncSession = Depends(get_db),
):
    """Admin endpoint: Update a project."""
    db_project = await db.get(Project, id)
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")

    update_data = project_in.model_dump(exclude_unset=True)

    # Check if slug is being updated and if it conflicts with existing records
    if "slug" in update_data:
        existing = await db.scalar(
            select(Project).where(Project.slug == update_data["slug"], Project.id != id)
        )
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"Project with slug '{update_data['slug']}' already exists",
            )

    # Validate category if being updated
    if "category_id" in update_data:
        category = await db.get(Category, update_data["category_id"])
        if not category:
            raise HTTPException(
                status_code=404,
                detail=f"Category with id '{update_data['category_id']}' not found",
            )

    for field, value in update_data.items():
        setattr(db_project, field, value)

    await db.commit()

    return {"data": ProjectResponse.model_validate(await reload(db, id))}


@router.delete("/admin/projects/{id}", status_code=204)
async def delete_project(
    id: UUID,
    _admin: CurrentAdmin,
    db: AsyncSession = Depends(get_db),
):
    """Admin endpoint: Delete a project."""
    project = await db.get(Project, id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    await db.delete(project)
    await db.commit()
    return None


@router.patch("/admin/projects/reorder")
async def reorder_projects(
    request: ProjectReorderRequest,
    _admin: CurrentAdmin,
    db: AsyncSession = Depends(get_db),
):
    """Admin endpoint: Reorder projects."""
    updated_count = 0
    for item in request.orders:
        result = await db.execute(
            update(Project)
            .where(Project.id == item.id)
            .values(order=item.order)
            .returning(Project.id)
        )
        updated_count += len(result.all())
    await db.commit()
    return {
        "data": {"message": "Projects reordered successfully", "updated": updated_count}
    }


# ============================================================================
# Public Routes (no authentication required)
# ============================================================================


@router.get("/projects")
async def get_projects(
    db: AsyncSession = Depends(get_db),
    category_id: UUID | None = Query(None, description="Filter by category UUID"),
    featured: bool | None = Query(None),
    tag: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Public endpoint: Get only published projects."""
    query = select(Project).where(Project.published.is_(True))

    if category_id:
        query = query.where(Project.category_id == category_id)
    if featured is not None:
        query = query.where(Project.featured == featured)
    if tag:
        query = query.where(Project.tags.contains([tag]))

    total = await count(db, query)
    projects = await db.scalars(
        query.options(WITH_CATEGORY)
        .order_by(*PROJECT_DISPLAY_ORDER)
        .offset(offset)
        .limit(limit)
    )

    return {
        "data": [ProjectResponse.model_validate(p) for p in projects],
        "pagination": offset_pagination(offset, limit, total),
    }


@router.get("/projects/{slug}")
async def get_project(
    slug: str = Path(..., min_length=1, max_length=255),
    db: AsyncSession = Depends(get_db),
):
    """Public endpoint: Get a single published project by slug."""
    project = await db.scalar(
        select(Project)
        .options(WITH_CATEGORY)
        .where(Project.slug == slug, Project.published.is_(True))
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"data": ProjectResponse.model_validate(project)}
