from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from core.paginator import paginate
from db.models.category import Category
from db.models.projects import Project
from db.rows import NotFoundError, ensure_unique, get_or_raise
from schemas.projects import ProjectCreate, ProjectResponse, ProjectUpdate

WHAT = "Project"

# Display order: the `order` the admin sets on each project, then newest first.
# id breaks any remaining tie so offset pagination is stable.
PROJECT_DISPLAY_ORDER = (Project.order, Project.created_at.desc(), Project.id)

# ProjectResponse reads the category, which an async session cannot load on
# access (Project.category_rel is lazy="raise"): every query that returns
# projects loads it with them.
WITH_CATEGORY = joinedload(Project.category_rel)


async def reload(db: AsyncSession, id: UUID) -> Project:
    """The project as the database has it now, with its category, after a
    commit. The query fills in what the session has expired or never loaded
    (updated_at, set by the UPDATE; the category); populate_existing also
    overwrites what it did load, so a category read before a change of
    category_id cannot be returned stale."""
    project = await db.scalar(
        select(Project)
        .options(WITH_CATEGORY)
        .where(Project.id == id)
        .execution_options(populate_existing=True)
    )
    if project is None:
        raise NotFoundError(f"{WHAT} not found")
    return project


async def page(
    db: AsyncSession,
    *,
    published: bool | None,
    category_id: UUID | None,
    featured: bool | None,
    tag: str | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """A page of projects, filtered as the works page asks: only published
    ones or only unpublished ones, or both if `published` is None."""
    query = select(Project)
    if published is not None:
        query = query.where(Project.published.is_(published))
    if category_id:
        query = query.where(Project.category_id == category_id)
    if featured is not None:
        query = query.where(Project.featured == featured)
    if tag:
        query = query.where(Project.tags.contains([tag]))
    query = query.options(WITH_CATEGORY).order_by(*PROJECT_DISPLAY_ORDER)
    return await paginate(db, query, ProjectResponse, offset, limit)


async def get(db: AsyncSession, id: UUID) -> Project:
    """The project, published or not, with its category."""
    return await reload(db, id)


async def get_by_slug(db: AsyncSession, slug: str) -> Project:
    """The project with this slug, published or not, with its category."""
    project = await db.scalar(
        select(Project).options(WITH_CATEGORY).where(Project.slug == slug)
    )
    if project is None:
        raise NotFoundError(f"{WHAT} not found")
    return project


async def get_published(db: AsyncSession, slug: str) -> Project:
    project = await db.scalar(
        select(Project)
        .options(WITH_CATEGORY)
        .where(Project.slug == slug, Project.published.is_(True))
    )
    if project is None:
        raise NotFoundError(f"{WHAT} not found")
    return project


async def ensure_category(db: AsyncSession, id: UUID) -> None:
    if await db.get(Category, id) is None:
        raise NotFoundError(f"Category with id '{id}' not found")


async def create(db: AsyncSession, fields: ProjectCreate) -> Project:
    await ensure_unique(db, Project.slug, fields.slug, WHAT)
    await ensure_category(db, fields.category_id)
    project = Project(**fields.model_dump())
    db.add(project)
    await db.commit()
    return await reload(db, project.id)


async def update(db: AsyncSession, id: UUID, fields: ProjectUpdate) -> Project:
    """Sets the fields `fields` was given, and no others."""
    project = await get_or_raise(db, Project, id, WHAT)
    changes = fields.model_dump(exclude_unset=True)
    if "slug" in changes:
        await ensure_unique(db, Project.slug, changes["slug"], WHAT, exclude_id=id)
    if "category_id" in changes:
        await ensure_category(db, changes["category_id"])
    for field, value in changes.items():
        setattr(project, field, value)
    await db.commit()
    return await reload(db, id)


async def delete(db: AsyncSession, id: UUID) -> None:
    await db.delete(await get_or_raise(db, Project, id, WHAT))
    await db.commit()
