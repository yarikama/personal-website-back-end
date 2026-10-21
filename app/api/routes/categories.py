"""Category CRUD routes."""

from uuid import UUID

from fastapi import APIRouter

from api.dependencies import CurrentAdmin
from db.dependency import DB
from schemas.category import CategoryCreate, CategoryResponse, CategoryUpdate
from services.content import categories

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
    rows = await categories.all_categories(db)
    return {"data": [CategoryResponse.model_validate(c) for c in rows]}


@router.post("/admin/categories", status_code=201)
async def create_category(
    category: CategoryCreate,
    _admin: CurrentAdmin,
    db: DB,
):
    """Admin endpoint: Create a new category."""
    created = await categories.create(db, category)
    return {"data": CategoryResponse.model_validate(created)}


@router.put("/admin/categories/{id}")
async def update_category(
    id: UUID,
    category: CategoryUpdate,
    _admin: CurrentAdmin,
    db: DB,
):
    """Admin endpoint: Update a category."""
    updated = await categories.update(db, id, category)
    return {"data": CategoryResponse.model_validate(updated)}


@router.delete("/admin/categories/{id}", status_code=204)
async def delete_category(
    id: UUID,
    _admin: CurrentAdmin,
    db: DB,
):
    """Admin endpoint: Delete a category, if no project is in it (409)."""
    await categories.delete(db, id)


# ============================================================================
# Public Routes (no authentication required)
# ============================================================================


@router.get("/categories")
async def get_categories(db: DB):
    """Public endpoint: the works page's filters, see categories.with_counts."""
    return {"data": await categories.with_counts(db)}
