"""Category CRUD routes."""

from uuid import UUID

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from api.crud import ensure_unique, get_or_404
from api.dependencies import CurrentAdmin
from core.paginator import count
from db.dependency import DB
from db.models.category import Category
from db.models.projects import Project
from schemas.category import (
    CategoryCreate,
    CategoryResponse,
    CategoryUpdate,
    CategoryWithCount,
)

router = APIRouter()

# ============================================================================
# Admin Routes (require authentication)
# ============================================================================


@router.get("/admin/categories")
async def get_all_categories_admin(
    _admin: CurrentAdmin,
    db: DB,
):
    """Admin endpoint: Get all categories."""
    categories = await db.scalars(
        select(Category).order_by(Category.order, Category.label)
    )
    return {"data": [CategoryResponse.model_validate(c) for c in categories]}


@router.post("/admin/categories", status_code=201)
async def create_category(
    category: CategoryCreate,
    _admin: CurrentAdmin,
    db: DB,
):
    """Admin endpoint: Create a new category."""
    await ensure_unique(db, Category.name, category.name, "Category")

    db_category = Category(**category.model_dump())
    db.add(db_category)
    await db.commit()
    await db.refresh(db_category)
    return {"data": CategoryResponse.model_validate(db_category)}


@router.put("/admin/categories/{id}")
async def update_category(
    id: UUID,
    category: CategoryUpdate,
    _admin: CurrentAdmin,
    db: DB,
):
    """Admin endpoint: Update a category."""
    db_category = await get_or_404(db, Category, id, "Category")

    update_data = category.model_dump(exclude_unset=True)
    if "name" in update_data:
        await ensure_unique(
            db, Category.name, update_data["name"], "Category", exclude_id=id
        )

    for field, value in update_data.items():
        setattr(db_category, field, value)

    await db.commit()
    await db.refresh(db_category)
    return {"data": CategoryResponse.model_validate(db_category)}


@router.delete("/admin/categories/{id}", status_code=204)
async def delete_category(
    id: UUID,
    _admin: CurrentAdmin,
    db: DB,
):
    """Admin endpoint: Delete a category."""
    db_category = await get_or_404(db, Category, id, "Category")

    # Check if category has projects
    project_count = await count(db, select(Project).where(Project.category_id == id))
    if project_count > 0:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot delete category with {project_count} project(s). "
                "Please reassign or delete projects first."
            ),
        )

    await db.delete(db_category)
    await db.commit()


# ============================================================================
# Public Routes (no authentication required)
# ============================================================================


@router.get("/categories")
async def get_categories(db: DB):
    """
    Public endpoint: every category with its number of published projects,
    after an "All" entry that counts them all (the works page's filters).
    "All" takes its dates from the first category; with no category there is
    nothing to filter and no "All".
    """
    categories = list(
        await db.scalars(select(Category).order_by(Category.order, Category.label))
    )
    if not categories:
        return {"data": []}

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
    return {
        "data": [everything]
        + [
            CategoryWithCount(
                **CategoryResponse.model_validate(c).model_dump(),
                count=counts.get(c.id, 0),
            )
            for c in categories
        ]
    }
