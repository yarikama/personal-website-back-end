from uuid import UUID

from api.crud import ensure_unique, get_or_404
from api.dependencies import CurrentAdmin
from core.paginator import paginate
from db.dependency import DB
from db.models.category import Category
from db.models.projects import Project
from fastapi import APIRouter, HTTPException, Path, Query
from schemas.projects import ProjectCreate, ProjectResponse, ProjectUpdate
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

router = APIRouter()

# Display order: the `order` the admin sets on each project, then newest first.
# id breaks any remaining tie so offset pagination is stable.
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


async def list_projects(
    db: AsyncSession,
    query: Select,
    category_id: UUID | None,
    featured: bool | None,
    tag: str | None,
    limit: int,
    offset: int,
) -> dict:
    """A page of `query`'s projects, filtered as the works page asks."""
    if category_id:
        query = query.where(Project.category_id == category_id)
    if featured is not None:
        query = query.where(Project.featured == featured)
    if tag:
        query = query.where(Project.tags.contains([tag]))
    query = query.options(WITH_CATEGORY).order_by(*PROJECT_DISPLAY_ORDER)
    return await paginate(db, query, ProjectResponse, offset, limit)


async def ensure_category(db: AsyncSession, id: UUID) -> None:
    if await db.get(Category, id) is None:
        raise HTTPException(
            status_code=404, detail=f"Category with id '{id}' not found"
        )


# ============================================================================
# Admin Routes (require authentication)
# ============================================================================


@router.get("/admin/projects")
async def get_all_projects_admin(
    _admin: CurrentAdmin,
    db: DB,
    category_id: UUID | None = Query(None, description="Filter by category UUID"),
    featured: bool | None = Query(None),
    tag: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Admin endpoint: Get all projects including unpublished."""
    return await list_projects(
        db, select(Project), category_id, featured, tag, limit, offset
    )


@router.post("/admin/projects", status_code=201)
async def create_project(project_in: ProjectCreate, _admin: CurrentAdmin, db: DB):
    """Admin endpoint: Create a new project."""
    await ensure_unique(db, Project.slug, project_in.slug, "Project")
    await ensure_category(db, project_in.category_id)

    project = Project(**project_in.model_dump())
    db.add(project)
    await db.commit()

    return {"data": ProjectResponse.model_validate(await reload(db, project.id))}


@router.put("/admin/projects/{id}")
async def update_project(
    id: UUID, project_in: ProjectUpdate, _admin: CurrentAdmin, db: DB
):
    """Admin endpoint: Update a project."""
    db_project = await get_or_404(db, Project, id, "Project")

    update_data = project_in.model_dump(exclude_unset=True)
    if "slug" in update_data:
        await ensure_unique(
            db, Project.slug, update_data["slug"], "Project", exclude_id=id
        )
    if "category_id" in update_data:
        await ensure_category(db, update_data["category_id"])

    for field, value in update_data.items():
        setattr(db_project, field, value)

    await db.commit()

    return {"data": ProjectResponse.model_validate(await reload(db, id))}


@router.delete("/admin/projects/{id}", status_code=204)
async def delete_project(id: UUID, _admin: CurrentAdmin, db: DB):
    """Admin endpoint: Delete a project."""
    await db.delete(await get_or_404(db, Project, id, "Project"))
    await db.commit()


# ============================================================================
# Public Routes (no authentication required)
# ============================================================================


@router.get("/projects")
async def get_projects(
    db: DB,
    category_id: UUID | None = Query(None, description="Filter by category UUID"),
    featured: bool | None = Query(None),
    tag: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Public endpoint: Get only published projects."""
    published = select(Project).where(Project.published.is_(True))
    return await list_projects(db, published, category_id, featured, tag, limit, offset)


@router.get("/projects/{slug}")
async def get_project(db: DB, slug: str = Path(..., min_length=1, max_length=255)):
    """Public endpoint: Get a single published project by slug."""
    project = await db.scalar(
        select(Project)
        .options(WITH_CATEGORY)
        .where(Project.slug == slug, Project.published.is_(True))
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"data": ProjectResponse.model_validate(project)}
