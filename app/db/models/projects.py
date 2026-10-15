import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from db.session import Base

if TYPE_CHECKING:
    from db.models.category import Category


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str] = mapped_column(String(255), unique=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String))

    # Foreign key to categories table
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id")
    )

    # Legacy field - kept for backward compatibility during migration
    # Can be removed after migration is complete
    category: Mapped[str | None] = mapped_column(String(50))

    year: Mapped[str] = mapped_column(String(20))
    cover_image: Mapped[str | None] = mapped_column(String(500))
    link: Mapped[str | None] = mapped_column(String(500))
    github: Mapped[str | None] = mapped_column(String(500))
    metrics: Mapped[str | None] = mapped_column(Text)
    formula: Mapped[str | None] = mapped_column(Text)
    # Nullable in the database; new rows always get a value.
    featured: Mapped[bool | None] = mapped_column(Boolean, default=False)
    order: Mapped[int | None] = mapped_column(Integer, default=0)
    published: Mapped[bool | None] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now()
    )

    # Relationship. An async session cannot load it when it is read, so a
    # query that needs it says so (joinedload); lazy="raise" makes one that
    # forgot fail plainly instead of with MissingGreenlet.
    category_rel: Mapped["Category"] = relationship(
        back_populates="projects", lazy="raise"
    )
