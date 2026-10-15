from datetime import datetime
from uuid import UUID

from pydantic import Field

from schemas.base import BaseSchema
from schemas.category import CategoryResponse


class ProjectBase(BaseSchema):
    slug: str
    title: str
    description: str
    tags: list[str]
    year: str
    cover_image: str | None = None
    link: str | None = None
    github: str | None = None
    metrics: str | None = None
    formula: str | None = None
    featured: bool
    order: int
    published: bool


class ProjectCreate(ProjectBase):
    category_id: UUID = Field(..., description="Category UUID")


class ProjectUpdate(BaseSchema):
    slug: str | None = None
    title: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    category_id: UUID | None = Field(None, description="Category UUID")
    year: str | None = None
    cover_image: str | None = None
    link: str | None = None
    github: str | None = None
    metrics: str | None = None
    formula: str | None = None
    featured: bool | None = None
    order: int | None = None
    published: bool | None = None


class ProjectResponse(ProjectBase):
    id: UUID
    # Read from the model's category_rel; sent as "category".
    category: CategoryResponse = Field(
        ..., validation_alias="category_rel", description="Full category object"
    )
    created_at: datetime
    updated_at: datetime
