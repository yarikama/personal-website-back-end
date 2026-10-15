import datetime as dt
import uuid

from sqlalchemy import Boolean, Date, DateTime, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from db.session import Base


class LabNote(Base):
    __tablename__ = "lab_notes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str] = mapped_column(String(255), unique=True)
    title: Mapped[str] = mapped_column(String(255))
    excerpt: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String))
    read_time: Mapped[str] = mapped_column(String(50))
    date: Mapped[dt.date] = mapped_column(Date)
    # Nullable in the database; new rows always get a value.
    published: Mapped[bool | None] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime | None] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now()
    )
