from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.paginator import count
from db.models.category import Category
from db.models.projects import Project
from db.rows import ConflictError, NotFoundError, ensure_unique, get_or_raise
from schemas.category import (
    CategoryCreate,
    CategoryResponse,
    CategoryUpdate,
    CategoryWithCount,
)

WHAT = "Category"


async def all_categories(db: AsyncSession) -> list[Category]:
    """Every category, in display order."""
    return list(
        await db.scalars(select(Category).order_by(Category.order, Category.label))
    )


async def get(db: AsyncSession, id: UUID) -> Category:
    return await get_or_raise(db, Category, id, WHAT)


async def get_by_name(db: AsyncSession, name: str) -> Category:
    category = await db.scalar(select(Category).where(Category.name == name))
    if category is None:
        raise NotFoundError(f"Category '{name}' not found")
    return category


async def with_counts(db: AsyncSession) -> list[CategoryWithCount]:
    """
    Every category with its number of published projects, after an "All"
    entry that counts them all (the works page's filters). "All" takes its
    dates from the first category; with no category there is nothing to
    filter and no "All".
    """
    categories = await all_categories(db)
    if not categories:
        return []

    rows = await db.execute(
        select(Project.category_id, func.count())
        .where(Project.published.is_(True))
        .group_by(Project.category_id)
    )
    counts = dict(rows.tuples().all())
    first = categories[0]
    everything = CategoryWithCount(
        id=UUID(int=0),
        name="all",
        label="All",
        description="All projects",
        order=-1,
        count=sum(counts.values()),
        created_at=first.created_at,
        updated_at=first.updated_at,
    )
    return [everything] + [
        CategoryWithCount(
            **CategoryResponse.model_validate(c).model_dump(),
            count=counts.get(c.id, 0),
        )
        for c in categories
    ]


async def create(db: AsyncSession, fields: CategoryCreate) -> Category:
    await ensure_unique(db, Category.name, fields.name, WHAT)
    category = Category(**fields.model_dump())
    db.add(category)
    await db.commit()
    await db.refresh(category)
    return category


async def update(db: AsyncSession, id: UUID, fields: CategoryUpdate) -> Category:
    """Sets the fields `fields` was given, and no others."""
    category = await get_or_raise(db, Category, id, WHAT)
    changes = fields.model_dump(exclude_unset=True)
    if "name" in changes:
        await ensure_unique(db, Category.name, changes["name"], WHAT, exclude_id=id)
    for field, value in changes.items():
        setattr(category, field, value)
    await db.commit()
    await db.refresh(category)
    return category


async def delete(db: AsyncSession, id: UUID) -> None:
    """Deletes the category; ConflictError while projects are still in it."""
    category = await get_or_raise(db, Category, id, WHAT)
    projects = await count(db, select(Project).where(Project.category_id == id))
    if projects > 0:
        raise ConflictError(
            f"Cannot delete category with {projects} project(s). "
            "Please reassign or delete projects first."
        )
    await db.delete(category)
    await db.commit()
