"""Category model for project categorization."""

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.session import Base

if TYPE_CHECKING:
    from db.models.projects import Project


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Category(Base):
    """Category model."""

    __tablename__ = "categories"
    # The table was created with both a unique constraint and a unique index on
    # name (4b2c8d0e1f23). The index alone would do; declaring the constraint
    # too keeps the model matching the database, so autogenerate does not
    # propose dropping it.
    __table_args__ = (UniqueConstraint("name", name="categories_name_key"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    order: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    # Relationship. Not loaded on access, which an async session cannot do
    # (see Project.category_rel).
    projects: Mapped[list["Project"]] = relationship(
        back_populates="category_rel", lazy="raise"
    )
